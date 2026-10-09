"""Training pipeline entry points.

Each stage is a function taking a ``CheckpointStore`` and returning its
metrics. Stages are executed by ``run_pipeline`` in order; completed stages
are loaded from the store and skipped, so an interrupted Colab session
resumes exactly where it stopped.

Stage map (MODEL_LIFECYCLE.md):

    ingest      load + audit Recruit                -> row/store counts
    baseline    day-of-week historical average      -> MAE, RMSE, RMSLE
    features    leakage-safe feature build          -> rows, features, version
    fallbacks   linear/RF/XGBoost vs primaries      -> MAE, RMSE, RMSLE
    lightgbm    primary model vs baseline           -> MAE, RMSE, RMSLE, lift
    catboost    secondary primary vs lightgbm       -> MAE, RMSE, RMSLE, delta
    conformal   adaptive conformal intervals        -> coverage, width
    policy      preparation policies SR/PS/UD       -> exposures per policy
    shap        per-forecast contributions          -> top features, disclaimer
    backtest    rolling-origin folds (6x14d)        -> per-fold + mean/std metrics

The chronological split (Section 3.11): everything before ``--test-start``
trains; the test window is held out untouched until final evaluation. The
conformal loop calibrates its warmup on the TRAIN validation tail and runs
online over the test window; policy quantiles are fixed before that window.
CatBoost is the comparison/fallback primary; conformal and policy run on
the LightGBM outputs by default, with the winner selection left to the
governance flow (MODEL_LIFECYCLE.md section 8), not to this pipeline.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from diq_model.artifacts import CheckpointStore, sha256_of_path
from diq_model.constants import CALENDAR_FILE, RESERVE_FILE, STORE_FILE, VISIT_FILE
from diq_model.data.audit import audit_recruit
from diq_model.data.load import load_recruit
from diq_model.evaluate.metrics import all_metrics
from diq_model.evaluate.tables import render_markdown_table
from diq_model.features.build import load_feature_frame
from diq_model.models.baselines import (
    DayOfWeekBaseline,
    GlobalAverageBaseline,
    HistoricalAverageBaseline,
    NaiveBaseline,
    SeasonalNaiveBaseline,
)
from diq_model.models.catboost_model import CatBoostModel
from diq_model.models.lightgbm_model import LightGBMModel
from diq_model.policy.simulate import (
    DEFAULT_RISK_WEIGHT,
    Policy,
    PolicyKind,
    newsvendor_level,
    simulate_policies,
    tradeoff_table,
)
from diq_model.repro import DEFAULT_SEED, set_global_seed
from diq_model.uncertainty.conformal import (
    AdaptiveConformal,
    CoverageByLevel,
    StratifiedAdaptiveConformal,
)

logger = logging.getLogger(__name__)

# Hard data-integrity checks: these ALWAYS block training.
HARD_CHECKS = {
    "duplicate_store_days",
    "date_parse_failures",
    "visitor_value_range",
    "identifier_consistency",
}
# Checks that compare against the audited FULL-dataset figures (Section 3.4.1).
# They block in strict mode only; subset runs (smoke tests) waive them.
REFERENCE_CHECKS = {"audited_scale", "temporal_span"}

# Section 3.17: cost ratios the risk-aware policy competes at on validation.
# One leftover portion costs 1 by normalisation; these are the costs of leaving
# one customer unserved, from "cheaper than a leftover" (0.5) to "much more".
RISK_WEIGHT_SWEEP: tuple[float, ...] = (0.5, 1.0, 2.0, 3.0, 5.0)


# ----------------------------------------------------------------- stages
def stage_ingest(store: CheckpointStore, data_root: Path, seed: int, strict: bool = True) -> dict:
    """Load Recruit, run the Section 3.7.1 audit, persist normalised frames.

    ``strict=True`` (default, used by the CLI) blocks on every audit failure.
    ``strict=False`` blocks only on hard integrity failures and merely reports
    reference-figure deviations - the documented escape hatch for running the
    pipeline on a subset of the data (smoke tests, Colab dry runs).
    """
    data = load_recruit(data_root)

    report = audit_recruit(data)
    print(report.summary())
    blocking = set(report.failures) if strict else set(report.failures) & HARD_CHECKS
    if blocking:
        raise SystemExit(
            f"Audit failed: {', '.join(sorted(blocking))}\n"
            "Resolve data issues before training (Section 3.7)."
        )
    if not strict and set(report.failures) - HARD_CHECKS:
        logger.warning(
            "Reference-figure deviations reported (subset run): %s",
            ", ".join(set(report.failures) & REFERENCE_CHECKS),
        )

    store.save_dataframe(data.visits, "visits.parquet")
    store.save_dataframe(data.stores, "stores.parquet")
    store.save_dataframe(data.calendar, "calendar.parquet")
    if len(data.reservations):
        store.save_dataframe(data.reservations, "reservations.parquet")

    # Provenance (Section 3.27/3.28): pin the exact source files this run
    # consumed, so any later figure can be traced back to specific bytes.
    source_sha256 = {
        name: sha256_of_path(Path(data_root) / name)
        for name in (VISIT_FILE, STORE_FILE, CALENDAR_FILE, RESERVE_FILE)
        if (Path(data_root) / name).exists()
    }

    return {
        "visit_rows": data.n_rows,
        "stores": data.n_stores,
        "reservation_rows": len(data.reservations),
        "audit_checks": len(report.checks),
        "source_sha256": source_sha256,
    }


def stage_baseline(store: CheckpointStore, seed: int, test_start: str) -> dict:
    """Evaluate ALL baselines from Section 3.12 / EVALUATION.md on the same
    chronological window - the gauntlet a model must beat to reach
    production:

        naive              yesterday's demand
        seasonal_naive     the same weekday last week
        historical_average mean of the last 28 comparable days (frozen)
        weekday_mean       per-store, per-weekday historical mean
        global_average     the floor

    All baselines fit on the train window only and score the same test
    days. Metrics include MASE and Bias (EVALUATION.md "Metrics"), with
    the MASE scale taken from the TRAIN series (never the test window).

    ``weekday_mean`` predictions are persisted as
    ``baseline_predictions.parquet`` for downstream stages, so "baseline"
    keeps its established meaning: the strongest simple signal, and the
    number a manager approximates in their head.
    """
    visits = store.load_dataframe("visits.parquet")
    split = pd.Timestamp(test_start)

    train = visits[visits["visit_date"] < split]
    test = visits[visits["visit_date"] >= split]
    if train.empty or test.empty:
        raise SystemExit(f"Empty split at {test_start!r}; check --test-start.")

    y_test = test["visitors"]
    y_train = train["visitors"]

    candidates = {
        "naive": NaiveBaseline(),
        "seasonal_naive": SeasonalNaiveBaseline(),
        "historical_average": HistoricalAverageBaseline(lookback_days=28),
        "weekday_mean": DayOfWeekBaseline(),
        "global_average": GlobalAverageBaseline(),
    }

    per_baseline: dict[str, dict] = {}
    persisted: pd.Series | None = None
    for name, model in candidates.items():
        preds = model.fit(train).predict(test)
        row = all_metrics(y_test, preds, y_train=y_train)
        row["test_rows"] = len(test)
        row["test_days"] = int(test["visit_date"].nunique())
        per_baseline[name] = row
        if name == "weekday_mean":
            persisted = preds

    # Persist the weekday-mean predictions for conformal/policy stages.
    out = test[["air_store_id", "visit_date", "visitors"]].copy()
    out["prediction"] = persisted.to_numpy()
    store.save_dataframe(out, "baseline_predictions.parquet")

    # Order by MAE: the bar a candidate model must clear is the best of these.
    ranking = sorted(per_baseline.items(), key=lambda kv: kv[1]["mae"])
    best_name, best = ranking[0]

    # Chapter-4 table: persisted as parquet + markdown beside the metrics.
    table_df = pd.DataFrame([{"baseline": name, **row} for name, row in ranking])
    store.save_dataframe(table_df, "baseline_table.parquet")
    store.save_text(
        render_markdown_table(table_df),
        "baseline_table.md",
    )

    return {
        "baselines": per_baseline,
        "best_baseline": best_name,
        "best_baseline_mae": best["mae"],
        "best_baseline_rmsle": best["rmsle"],
    }


def stage_features(store: CheckpointStore, seed: int, test_start: str) -> dict:
    """Build the leakage-safe feature frame from ingested visits."""
    from diq_model.features.build import build_features, save_feature_frame

    visits = store.load_dataframe("visits.parquet")
    calendar = None
    if store.has("calendar.parquet"):
        calendar = store.load_dataframe("calendar.parquet")
    reservations = None
    if store.has("reservations.parquet"):
        reservations = store.load_dataframe("reservations.parquet")

    fframe = build_features(visits, calendar=calendar, reservations=reservations)
    save_feature_frame(store, fframe)
    return {
        "rows": fframe.n_rows,
        "features": len(fframe.feature_columns),
        "feature_set_version": fframe.feature_set_version,
    }


def stage_fallbacks(store: CheckpointStore, seed: int, test_start: str) -> dict:
    """Train the Section 3.13 fallback models on the primary feature frame.

    Linear Regression, Random Forest and XGBoost are kept in the study "as
    baselines and fallback models" — they answer whether the boosted trees'
    complexity is justified, and they are the family to reach for if LightGBM
    or CatBoost prove unstable. They see exactly the same rows, features,
    chronological split and ``log1p`` target as the primaries, so the only
    variable in the comparison is the estimator.

    A model whose dependency is absent (XGBoost needs the ``training`` extra)
    is reported under ``skipped`` with its reason and left out of the ranking,
    rather than quietly replaced.
    """
    from diq_model.models.fallback_models import build_fallbacks, missing_dependencies

    fframe = load_feature_frame(store)
    df = fframe.frame
    split = pd.Timestamp(test_start)
    train_df = df[df["visit_date"] < split]
    test_df = df[df["visit_date"] >= split]
    if train_df.empty or test_df.empty:
        raise SystemExit(f"Empty split at {test_start!r}; check --test-start.")

    skipped = missing_dependencies()
    models = build_fallbacks()
    y_train = train_df["visitors"]

    per_model: dict[str, dict] = {}
    for name, model in models.items():
        model.fit(train_df, feature_columns=fframe.feature_columns, target="visitors")
        row = all_metrics(test_df["visitors"], model.predict(test_df), y_train=y_train)
        row["test_rows"] = len(test_df)
        per_model[name] = row

    if not per_model:  # pragma: no cover - guarded by the extra being installed
        raise SystemExit("No fallback model could be built: " + "; ".join(skipped.values()))

    # Order by MAE: the fallback that comes closest to the primaries' bar.
    ranking = sorted(per_model.items(), key=lambda kv: kv[1]["mae"])
    best_name, best = ranking[0]

    table_df = pd.DataFrame([{"model": name, **row} for name, row in ranking])
    store.save_dataframe(table_df, "fallback_table.parquet")
    store.save_text(render_markdown_table(table_df), "fallback_table.md")

    return {
        "fallbacks": per_model,
        "best_fallback": best_name,
        "best_fallback_mae": best["mae"],
        "best_fallback_rmsle": best["rmsle"],
        "skipped": skipped,
    }


def stage_lightgbm(store: CheckpointStore, seed: int, test_start: str) -> dict:
    """Train LightGBM (Sections 3.13-3.14) and compare against the baseline.

    Chronological discipline: train on features before ``test_start``; the
    final 10% of the TRAIN window is the early-stopping validation slice.
    The test window is scored exactly once, after training completes.
    """
    from diq_model.models.lightgbm_model import LightGBMModel

    fframe = load_feature_frame(store)
    df = fframe.frame
    split = pd.Timestamp(test_start)

    train_df = df[df["visit_date"] < split]
    test_df = df[df["visit_date"] >= split]
    if train_df.empty or test_df.empty:
        raise SystemExit(f"Empty split at {test_start!r}; check --test-start.")

    # Chronological early-stopping slice: the tail of the training window.
    cutoff = train_df["visit_date"].quantile(0.9)
    fit_df = train_df[train_df["visit_date"] < cutoff]
    valid_df = train_df[train_df["visit_date"] >= cutoff]

    model = LightGBMModel().fit(
        fit_df,
        feature_columns=fframe.feature_columns,
        valid=valid_df,
        categorical=["dow", "month"],
    )
    # Validation golden set for the policy-quantile sweep: realised demand,
    # point forecasts and the GLOBAL conformal bounds computed on the
    # validation tail (its own warmup comes from the fit slice - the test
    # window never calibrates anything).
    store.save_dataframe(
        valid_df[["air_store_id", "visit_date", "visitors"]].assign(
            prediction=model.predict(valid_df)
        ),
        "validation_predictions.parquet",
    )
    model.save(store)

    # Score the held-out window once.
    preds = model.predict(test_df)
    metrics = all_metrics(test_df["visitors"], preds, y_train=train_df["visitors"])

    # Baseline comparison on the SAME feature-frame rows (same window):
    # the bar is the BEST baseline from the baseline stage, not just one.
    baseline_stage = store.stage_info("baseline") or {}
    best_name = baseline_stage.get("metrics", {}).get("best_baseline", "weekday_mean")
    refit_best = {
        "naive": NaiveBaseline(),
        "seasonal_naive": SeasonalNaiveBaseline(),
        "historical_average": HistoricalAverageBaseline(lookback_days=28),
        "weekday_mean": DayOfWeekBaseline(),
        "global_average": GlobalAverageBaseline(),
    }.get(best_name, DayOfWeekBaseline())
    refit = refit_best.fit(train_df)
    refit_preds = refit.predict(test_df)
    baseline_metrics = all_metrics(test_df["visitors"], refit_preds, y_train=train_df["visitors"])

    metrics.update(
        {
            "baseline_name": best_name,
            "baseline_mae": baseline_metrics["mae"],
            "baseline_rmsle": baseline_metrics["rmsle"],
            "mae_lift_vs_baseline": (
                round(1 - metrics["mae"] / baseline_metrics["mae"], 4)
                if baseline_metrics["mae"]
                else 0.0
            ),
            "test_rows": len(test_df),
            "best_iteration": model.best_iteration_,
            "feature_set_version": fframe.feature_set_version,
        }
    )

    # Persist test predictions for the conformal and policy stages.
    out = test_df[["air_store_id", "visit_date", "visitors"]].copy()
    out["prediction"] = preds
    store.save_dataframe(out, "lightgbm_predictions.parquet")
    return metrics


def stage_catboost(store: CheckpointStore, seed: int, test_start: str) -> dict:
    """Train CatBoost (Sections 3.13-3.14) and compare against LightGBM.

    Identical split, features and metric window as the lightgbm stage, so
    the comparison is like-for-like. Role: fallback primary - kept because
    the two boosted models fail differently (MODEL_LIFECYCLE.md section 4).
    """
    fframe = load_feature_frame(store)
    df = fframe.frame
    split = pd.Timestamp(test_start)

    train_df = df[df["visit_date"] < split]
    test_df = df[df["visit_date"] >= split]
    if train_df.empty or test_df.empty:
        raise SystemExit(f"Empty split at {test_start!r}; check --test-start.")

    cutoff = train_df["visit_date"].quantile(0.9)
    fit_df = train_df[train_df["visit_date"] < cutoff]
    valid_df = train_df[train_df["visit_date"] >= cutoff]

    model = CatBoostModel().fit(fit_df, feature_columns=fframe.feature_columns, valid=valid_df)
    model.save(store)

    preds = model.predict(test_df)
    metrics = all_metrics(test_df["visitors"], preds, y_train=train_df["visitors"])

    lgb_info = store.stage_info("lightgbm")
    lgb_metrics = (lgb_info or {}).get("metrics", {})
    metrics.update(
        {
            "test_rows": len(test_df),
            "best_iteration": int(model.best_iteration_),
            "feature_set_version": fframe.feature_set_version,
            "lightgbm_mae": lgb_metrics.get("mae"),
            "mae_delta_vs_lightgbm": (
                round(metrics["mae"] - lgb_metrics["mae"], 3)
                if lgb_metrics.get("mae") is not None
                else None
            ),
        }
    )

    out = test_df[["air_store_id", "visit_date", "visitors"]].copy()
    out["prediction"] = preds
    store.save_dataframe(out, "catboost_predictions.parquet")
    return metrics


def stage_conformal(store: CheckpointStore, seed: int, test_start: str) -> dict:
    """Adaptive conformal intervals over the held-out window (Section 3.16).

    Boundary discipline: the residual warmup comes from the TRAIN validation
    tail only (the same slice LightGBM early-stopped on); the Gibbs-Candès
    loop then runs online over the test window, where at each step the
    interval for day t sees only information from before t. The test window
    never calibrates anything.

    Intervals are STRATIFIED by predicted demand level (Mondrian ACI,
    uncertainty/conformal.py): one alpha and residual window per level, so
    the heavy-tailed 60+ bucket calibrates on its own scale instead of the
    quiet-day quantile that collapsed its coverage under the single global
    quantile. Thin buckets fall back to the global ACI.
    """
    fframe = load_feature_frame(store)
    df = fframe.frame
    split = pd.Timestamp(test_start)

    train_df = df[df["visit_date"] < split]
    test_df = df[df["visit_date"] >= split]
    if train_df.empty or test_df.empty:
        raise SystemExit(f"Empty split at {test_start!r}; check --test-start.")

    model = LightGBMModel.load(store)
    cutoff = train_df["visit_date"].quantile(0.9)
    valid_df = train_df[train_df["visit_date"] >= cutoff]

    # Test predictions come from the lightgbm stage's saved frame (same rows,
    # same split discipline) - the model is NOT re-scored here.
    lgb_preds = store.load_dataframe("lightgbm_predictions.parquet")
    if len(lgb_preds) != len(test_df):
        raise SystemExit(
            "lightgbm predictions do not match the feature frame rows; " "rerun with --force"
        )

    y_test = lgb_preds["visitors"].to_numpy(dtype="float64")
    p_test = lgb_preds["prediction"].to_numpy(dtype="float64")

    # Warmup residuals: train-window predictions vs train actuals.
    valid_pred = model.predict(valid_df)

    # --- stratified (Mondrian) intervals: the production intervals ---------
    saci = StratifiedAdaptiveConformal(target_coverage=0.90, gamma=0.02)
    intervals, bucket_labels = saci.calibrate_online(
        y_test,
        p_test,
        warmup_true=valid_df["visitors"].to_numpy(dtype="float64"),
        warmup_pred=valid_pred,
    )
    lo = np.array([iv[0] for iv in intervals])
    hi = np.array([iv[1] for iv in intervals])

    out = lgb_preds.copy()
    out["lower"], out["upper"] = lo, hi
    out["calibration_bucket"] = bucket_labels
    store.save_dataframe(out, "conformal_predictions.parquet")

    # --- global-quantile comparison run (the honesty baseline) ------------
    gaci = AdaptiveConformal(target_coverage=0.90, gamma=0.02)
    g_intervals = gaci.calibrate_online(
        y_test,
        p_test,
        warmup_true=valid_df["visitors"].to_numpy(dtype="float64"),
        warmup_pred=valid_pred,
    )
    g_lo = np.array([iv[0] for iv in g_intervals])
    g_hi = np.array([iv[1] for iv in g_intervals])

    cov = AdaptiveConformal.empirical_coverage(y_test, lo, hi)
    cov_by_level = CoverageByLevel().coverage(y_test, lo, hi)
    cov_by_level = {k: (None if np.isnan(v) else round(v, 4)) for k, v in cov_by_level.items()}
    strat_by_bucket = saci.coverage_by_bucket(y_test, lo, hi, p_test)
    strat_by_bucket = {
        k: (None if np.isnan(v) else round(v, 4)) for k, v in strat_by_bucket.items()
    }
    fallback_share = float(np.mean([lab == "global-fallback" for lab in bucket_labels]))

    # Validation-tail conformal bounds (global quantile): the tuning set for
    # the policy multiplier. The test window never participates.
    v_intervals = gaci.calibrate_online(
        valid_df["visitors"].to_numpy(dtype="float64"),
        valid_pred,
        warmup_true=train_df[train_df["visit_date"] < cutoff]["visitors"].to_numpy(dtype="float64")[
            -200:
        ],
        warmup_pred=model.predict(train_df[train_df["visit_date"] < cutoff].tail(200)),
    )
    store.save_dataframe(
        valid_df[["air_store_id", "visit_date", "visitors"]].assign(
            prediction=valid_pred,
            upper=np.array([iv[1] for iv in v_intervals]),
        ),
        "validation_conformal.parquet",
    )

    # ---- Section 3.16: behaviour at other coverage levels ------------------
    # The proposal names a nominal 90% interval as the primary configuration and
    # asks for sensitivity at other levels "where appropriate". The same online
    # loop is re-run on the SAME predictions at 80% and 95%, so the only thing
    # that changes is the target: the result shows whether the reported coverage
    # behaviour is a property of the method or of the chosen level. Cheap, and
    # it uses the test window for nothing - the warmup is still the train tail.
    coverage_sensitivity: dict[str, dict[str, float]] = {}
    for level in (0.80, 0.95):
        level_aci = StratifiedAdaptiveConformal(target_coverage=level, gamma=0.02)
        level_intervals, _ = level_aci.calibrate_online(
            y_test,
            p_test,
            warmup_true=valid_df["visitors"].to_numpy(dtype="float64"),
            warmup_pred=valid_pred,
        )
        l_lo = np.array([iv[0] for iv in level_intervals])
        l_hi = np.array([iv[1] for iv in level_intervals])
        coverage_sensitivity[f"{level:.2f}"] = {
            "target_coverage": level,
            "empirical_coverage": round(
                AdaptiveConformal.empirical_coverage(y_test, l_lo, l_hi), 4
            ),
            "mean_interval_width": round(AdaptiveConformal.mean_interval_width(l_lo, l_hi), 2),
        }

    return {
        "target_coverage": 0.90,
        "empirical_coverage": round(cov, 4),
        "mean_interval_width": round(AdaptiveConformal.mean_interval_width(lo, hi), 2),
        "coverage_level_sensitivity": coverage_sensitivity,
        "coverage_by_demand_level": cov_by_level,
        "coverage_by_predicted_bucket": strat_by_bucket,
        "global_quantile_comparison": {
            "empirical_coverage": round(
                AdaptiveConformal.empirical_coverage(y_test, g_lo, g_hi), 4
            ),
            "mean_interval_width": round(AdaptiveConformal.mean_interval_width(g_lo, g_hi), 2),
            "coverage_by_demand_level": {
                k: (None if np.isnan(v) else round(v, 4))
                for k, v in CoverageByLevel().coverage(y_test, g_lo, g_hi).items()
            },
        },
        "global_fallback_share": round(fallback_share, 4),
        "scored_days": int((~np.isnan(lo)).sum()),
    }


def stage_policy(store: CheckpointStore, seed: int, test_start: str) -> dict:
    """Preparation-policy simulation over the held-out window (3.17-3.20).

    Uses the conformal upper bound for the uncertainty-aware policy and the
    full visit history (shift(1) windows only) for the historical-average
    policy. The quantile is a pipeline constant fixed before the test window
    runs; sweeping it belongs to the validation stage, never the test.
    """
    visits = store.load_dataframe("visits.parquet")
    sim = store.load_dataframe("conformal_predictions.parquet")

    # Drop the first ACI day(s) with no interval: policies need the upper bound.
    sim = sim.dropna(subset=["upper"])
    if sim.empty:
        raise SystemExit("No conformal intervals available; run the conformal stage.")

    # ---- Section 3.14 discipline: tune the multiplier on VALIDATION only ---
    # Criterion: surplus + ud_weight * unmet, in visitor-equivalents. The
    # default weight 1.0 (equal costing) is itself a choice; a site that
    # prices stock-outs higher raises ud_weight and the sweep shifts toward
    # more padding. The point policy competes in the same sweep: if padding
    # cannot justify itself even at the best scale, the verdict says so.
    ud_weight = 1.0
    sweep: list[dict] = []
    chosen_scale = 1.0
    chosen_risk_weight = DEFAULT_RISK_WEIGHT
    verdict = "validation_unavailable_default_scale_1.0"
    # Signed calibration error for the risk-aware policy's newsvendor quantile.
    residuals: np.ndarray | None = None
    if store.has("validation_conformal.parquet"):
        vsim = store.load_dataframe("validation_conformal.parquet").dropna(subset=["upper"])
        if not vsim.empty:
            residuals = vsim["visitors"].to_numpy(dtype="float64") - vsim["prediction"].to_numpy(
                dtype="float64"
            )

            def cost(ps: float, ud: float) -> float:
                return round(ps + ud_weight * ud, 2)

            for scale in (0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.5):
                (r,) = simulate_policies(
                    vsim,
                    visits,
                    [Policy(PolicyKind.uncertainty_aware, quantile=scale)],
                )
                sweep.append(
                    {
                        "scale": scale,
                        "ps": r.surplus_ps,
                        "ud": r.unmet_ud,
                        "ps_ud": cost(r.surplus_ps, r.unmet_ud),
                    }
                )
            (pr,) = simulate_policies(vsim, visits, [Policy(PolicyKind.point_forecast)])
            sweep.append(
                {
                    "scale": "point_forecast",
                    "ps": pr.surplus_ps,
                    "ud": pr.unmet_ud,
                    "ps_ud": cost(pr.surplus_ps, pr.unmet_ud),
                }
            )

            # ---- Section 3.17: the risk-aware policy competes on the same
            # criterion, so "sufficient evidence supports its inclusion" is
            # decided by the validation window and not asserted here. Each
            # weight is a different cost ratio (stock-out / leftover).
            for weight in RISK_WEIGHT_SWEEP:
                (rr,) = simulate_policies(
                    vsim,
                    visits,
                    [Policy(PolicyKind.risk_aware, weight=weight)],
                    residuals=residuals,
                )
                sweep.append(
                    {
                        "scale": f"risk_w{weight:g}",
                        "weight": weight,
                        "ps": rr.surplus_ps,
                        "ud": rr.unmet_ud,
                        "ps_ud": cost(rr.surplus_ps, rr.unmet_ud),
                    }
                )

            best = min(sweep, key=lambda r: r["ps_ud"])
            best_key = str(best["scale"])
            least_padded = min(
                (r["scale"] for r in sweep if isinstance(r["scale"], float)),
            )
            if isinstance(best["scale"], float):
                verdict = f"uncertainty_aware_preferred_at_q{float(best['scale']):g}"
                chosen_scale = float(best["scale"])
            elif best_key.startswith("risk_w"):
                # The risk-aware policy won on validation: keep it at the
                # winning weight and retain the least-padded uncertainty-aware
                # rule as the mandated coverage-based comparison.
                verdict = f"risk_aware_preferred_at_w{float(best['weight']):g}"
                chosen_risk_weight = float(best["weight"])
                chosen_scale = least_padded
            else:
                # Padding never paid on validation under this costing; keep the
                # least-padded uncertainty-aware policy for the mandated
                # simulation and record the honest verdict.
                verdict = "point_forecast_preferred_under_this_costing"
                chosen_scale = least_padded
        else:
            chosen_scale = 1.0
    else:
        chosen_scale = 1.0

    results = simulate_policies(
        sim,
        visits,
        policies=[
            Policy(PolicyKind.historical_average, lookback_days=28),
            Policy(PolicyKind.point_forecast),
            Policy(PolicyKind.uncertainty_aware, quantile=chosen_scale),
            Policy(PolicyKind.risk_aware, weight=chosen_risk_weight),
        ],
        residuals=residuals,
    )
    table = tradeoff_table(results)
    store.save_object(table, "policy_results.joblib")

    by_policy = {row["policy"]: row for _, row in table.iterrows()}
    point = by_policy.get(PolicyKind.point_forecast.value, {})
    unc = by_policy.get(f"{PolicyKind.uncertainty_aware.value}-q{chosen_scale:g}", {})

    metrics = {
        "period_days": len(sim),
        "uncertainty_scale": chosen_scale,
        "risk_weight": chosen_risk_weight,
        "risk_newsvendor_level": round(newsvendor_level(chosen_risk_weight), 4),
        "scale_selection": {
            "criterion": "surplus_ps + ud_weight * unmet_ud (visitor-equivalents)",
            "ud_weight": ud_weight,
            "tuned_on": "validation tail (conformal bounds from the train window)",
            "verdict": verdict,
        },
        "scale_sweep_validation": sweep,
        "policies": {
            name: {
                "prepared_total": row["prepared_total"],
                "surplus_sr": row["surplus_sr"],
                "surplus_ps": row["surplus_ps"],
                "unmet_ud": row["unmet_ud"],
                "surplus_days": row["surplus_days"],
                "unmet_days": row["unmet_days"],
            }
            for name, row in by_policy.items()
        },
    }
    # The headline trade-off: uncertainty-aware buys UD reduction with surplus.
    if point is not None and unc is not None and point["unmet_ud"]:
        metrics["headline"] = {
            "ud_reduction_vs_point": round(1 - unc["unmet_ud"] / point["unmet_ud"], 4),
            "ps_increase_vs_point": round(unc["surplus_ps"] - point["surplus_ps"], 2),
        }
    return metrics


def stage_shap(store: CheckpointStore, seed: int, test_start: str) -> dict:
    """TreeSHAP contributions for the held-out window (Section 3.24).

    Explanations are computed for the LightGBM production candidate on the
    SAME rows it scored, and stored with the forecast identity
    (store, date) - so a later explanation request replays the stored
    values instead of re-deriving them against a newer model.
    Framing: predictive attribution, never causal effect.
    """
    from diq_model.explain.shap_values import (
        ATTRIBUTION_DISCLAIMER,
        explain_forecasts,
    )

    fframe = load_feature_frame(store)
    df = fframe.frame
    split = pd.Timestamp(test_start)
    test_df = df[df["visit_date"] >= split]
    if test_df.empty:
        raise SystemExit(f"Empty test window at {test_start!r}.")

    model = LightGBMModel.load(store)
    bundle = explain_forecasts(
        model,
        test_df,
        feature_columns=fframe.feature_columns,
        model_name="lightgbm",
    )

    # Store per-forecast contributions (long format) + global importance.
    store.save_dataframe(bundle.frame, "shap_contributions.parquet")
    store.save_object(
        {
            "model_name": bundle.model_name,
            "feature_set_version": bundle.feature_set_version,
            "disclaimer": ATTRIBUTION_DISCLAIMER,
        },
        "shap_meta.joblib",
    )

    global_imp = bundle.global_importance()
    top = global_imp.head(5).to_dict("records")

    # Additivity check in visitor units: prediction ≈ base + Σ contributions.
    # The residual is the log-space linearisation error, reported openly.
    per_row_sum = bundle.frame.groupby(["air_store_id", "visit_date"])["shap_impact"].sum()
    lgb_preds = store.load_dataframe("lightgbm_predictions.parquet")
    merged = lgb_preds.merge(
        per_row_sum.rename("shap_total").reset_index(),
        on=["air_store_id", "visit_date"],
    )
    reconstructed = bundle.base_value_visitors + merged["shap_total"]
    rel_err = (
        (merged["prediction"] - reconstructed).abs() / merged["prediction"].clip(lower=1)
    ).median()

    return {
        "rows_explained": len(test_df),
        "features_per_forecast": len(fframe.feature_columns),
        "top_features": top,
        "base_value_visitors": round(bundle.base_value_visitors, 3),
        "median_relative_reconstruction_error": round(float(rel_err), 4),
        "disclaimer": ATTRIBUTION_DISCLAIMER,
    }


def stage_sensitivity(store: CheckpointStore, seed: int, test_start: str) -> dict:
    """Section 3.25 sensitivity to feature groups and reservation availability.

    The proposal commits to investigating the sensitivity of the results to
    "feature groups" and "reservation availability", and Section 3.10 calls
    reservations supplementary because coverage is partial. Both are answered
    the same way: refit the SAME primary model (same hyperparameters, same
    seed, same chronological split, same rows) with one group's columns removed,
    so the only thing that changes is the information available.

    Two experiments:

    * leave-one-group-out ablation over ``FEATURE_GROUPS`` - the drop in MAE
      when a group is removed is that group's marginal contribution here, which
      is a statement about this dataset and not a causal claim;
    * the no-reservations variant, reported as its own named verdict because
      Section 3.10 makes it a product requirement ("the production model must
      not degrade on locations that have no reservation feed").

    Opt-in and expensive (one fit per subset), like the backtest stage.
    """
    from diq_model.features.build import FEATURE_GROUPS, feature_columns_for_groups
    from diq_model.models.lightgbm_model import LightGBMModel

    fframe = load_feature_frame(store)
    df = fframe.frame
    split = pd.Timestamp(test_start)
    train_df = df[df["visit_date"] < split]
    test_df = df[df["visit_date"] >= split]
    if train_df.empty or test_df.empty:
        raise SystemExit(f"Empty split at {test_start!r}; check --test-start.")

    cutoff = train_df["visit_date"].quantile(0.9)
    fit_df = train_df[train_df["visit_date"] < cutoff]
    valid_df = train_df[train_df["visit_date"] >= cutoff]

    full_columns = list(fframe.feature_columns)
    groups = [g for g in FEATURE_GROUPS if feature_columns_for_groups(full_columns, (g,))]

    def score(columns: list[str]) -> dict[str, float]:
        if not columns:
            raise ValueError("a feature subset with no columns cannot be fitted")
        # A leave-one-group-out subset may not carry every categorical feature
        # (dropping `calendar` removes `dow`/`month`), and LightGBM rejects a
        # categorical name that is absent from the frame. Pass the intersection.
        categorical = [c for c in ("dow", "month") if c in set(columns)]
        model = LightGBMModel().fit(
            fit_df,
            feature_columns=columns,
            valid=valid_df,
            categorical=categorical,
        )
        metrics = all_metrics(
            test_df["visitors"], model.predict(test_df), y_train=train_df["visitors"]
        )
        metrics["features"] = len(columns)
        return metrics

    full = score(full_columns)
    reference_mae = float(full["mae"]) or float("nan")

    ablation: dict[str, dict] = {}
    for group in groups:
        kept = [c for c in full_columns if c not in set(FEATURE_GROUPS[group])]
        dropped = len(full_columns) - len(kept)
        if not kept:
            # Dropping the only populated group would leave nothing to fit; the
            # honest report is that the experiment is undefined here.
            ablation[group] = {
                "dropped_features": dropped,
                "defined": False,
                "reason": "dropping this group leaves no predictors",
            }
            continue
        m = score(kept)
        ablation[group] = {
            "dropped_features": dropped,
            "defined": True,
            **m,
            # Positive = the model got WORSE without the group, i.e. the group
            # carried signal; negative = removing it helped (a real, publishable
            # outcome, not an error to hide).
            "mae_delta_vs_full": round(float(m["mae"]) - reference_mae, 3),
        }

    # The named product requirement, stated on its own.
    no_reservation_columns = [
        c for c in full_columns if c not in set(FEATURE_GROUPS["reservation"])
    ]
    has_reservations = no_reservation_columns != full_columns
    reservation_sensitivity: dict = {
        "reservation_features_present": has_reservations,
        "defined": has_reservations and bool(no_reservation_columns),
    }
    if reservation_sensitivity["defined"]:
        without = score(no_reservation_columns)
        reservation_sensitivity.update(
            {
                "with_reservations_mae": float(full["mae"]),
                "without_reservations_mae": float(without["mae"]),
                "mae_delta_without": round(float(without["mae"]) - reference_mae, 3),
                "verdict": (
                    "production_model_degrades_without_reservations"
                    if float(without["mae"]) - reference_mae > 0
                    else "reservations_are_supplementary"
                ),
            }
        )
    else:
        reservation_sensitivity["reason"] = (
            "this run's frame carries no reservation columns "
            "(no reservation feed, or none booked before the visit day)"
        )

    rows = [
        {
            "subset": "full",
            "features": len(full_columns),
            "mae": full["mae"],
            "rmse": full["rmse"],
            "rmsle": full["rmsle"],
            "mae_delta_vs_full": 0.0,
        }
    ] + [
        {
            "subset": f"without_{group}",
            "features": len(full_columns) - int(info.get("dropped_features", 0)),
            "mae": info.get("mae"),
            "rmse": info.get("rmse"),
            "rmsle": info.get("rmsle"),
            "mae_delta_vs_full": info.get("mae_delta_vs_full"),
        }
        for group, info in ablation.items()
    ]
    store.save_dataframe(pd.DataFrame(rows), "sensitivity_ablation.parquet")

    defined = {g: i for g, i in ablation.items() if i.get("defined")}
    most_important = (
        max(defined, key=lambda g: float(defined[g].get("mae_delta_vs_full") or 0.0))
        if defined
        else None
    )
    return {
        "rows": len(test_df),
        "features_full": len(full_columns),
        "full": full,
        "feature_group_ablation": ablation,
        "most_important_group": most_important,
        "reservation_availability": reservation_sensitivity,
        "note": (
            "marginal contribution on this dataset and split; not a causal " "claim (Section 3.24)"
        ),
    }


def stage_backtest(
    store: CheckpointStore,
    seed: int,
    test_start: str,
    n_folds: int = 6,
    test_days: int = 30,
    advance_days: int = 14,
    min_train_days: int = 180,
) -> dict:
    """Rolling-origin backtest of LightGBM (Section 3.25 sensitivity).

    EVALUATION.md: "6 folds, each advancing the origin by 14 days, so the
    reported figure is a mean over folds rather than a lucky window." Each
    fold refits LightGBM from scratch on that fold's train window and scores
    the held-out test window once; per-fold metrics and the mean/std summary
    are persisted as a table for the results chapter.

    Each fold also carries an interval, so the backtest reports **coverage**
    (the share of test days whose demand landed inside the prediction range)
    beside MAE. The interval is a split-conformal band calibrated on the same
    fold's held-out validation slice the early stop uses — never on the test
    window — so the number is an honest out-of-sample coverage, and it travels
    to the console's Experiments screen through the serving bundle.
    """
    from diq_model.evaluate.backtest import run_backtest

    fframe = load_feature_frame(store)
    df = fframe.frame
    target_coverage = 0.90

    def fit_predict(
        train: pd.DataFrame, test: pd.DataFrame
    ) -> tuple[pd.Series, np.ndarray, np.ndarray] | pd.Series:
        # Chronological early-stopping slice inside each fold's train window.
        cutoff = train["visit_date"].quantile(0.9)
        fit_df = train[train["visit_date"] < cutoff]
        valid_df = train[train["visit_date"] >= cutoff]
        model = LightGBMModel().fit(
            fit_df,
            feature_columns=fframe.feature_columns,
            valid=valid_df,
            categorical=["dow", "month"],
        )
        preds = pd.Series(model.predict(test), index=test.index)

        # Split-conformal half-width from the fold's own validation residuals,
        # with finite-sample (1 + 1/n) inflation and `method="higher"` so the
        # band is at least the target width — the same quantile rule serving
        # uses, kept here so the reported coverage is what the method achieves.
        if len(valid_df):
            val_pred = np.asarray(model.predict(valid_df), dtype="float64")
            residuals = np.abs(np.asarray(valid_df["visitors"], dtype="float64") - val_pred)
            residuals = residuals[np.isfinite(residuals)]
            if residuals.size:
                half = float(np.quantile(residuals, target_coverage, method="higher")) * (
                    1 + 1 / residuals.size
                )
                point = np.asarray(preds, dtype="float64")
                return preds, point - half, point + half
        return preds

    summary = run_backtest(
        df,
        fit_predict,
        n_folds=n_folds,
        test_days=test_days,
        advance_days=advance_days,
        min_train_days=min_train_days,
    )

    per_fold = pd.DataFrame(summary.pop("per_fold"))
    store.save_dataframe(per_fold, "backtest_folds.parquet")
    store.save_text(render_markdown_table(per_fold), "backtest_table.md")
    store.save_object(summary, "backtest_summary.joblib")

    return {
        "n_folds": summary["n_folds"],
        "mae_mean": summary["mae_mean"],
        "mae_std": summary["mae_std"],
        "rmsle_mean": summary["rmsle_mean"],
        "rmsle_std": summary["rmsle_std"],
        "coverage_mean": summary.get("coverage_mean"),
    }


def run_pipeline(
    store: CheckpointStore,
    data_root: Path,
    test_start: str,
    seed: int = DEFAULT_SEED,
    stages: list[str] | None = None,
    force: bool = False,
    strict: bool = True,
    n_folds: int = 6,
    test_days: int = 30,
    advance_days: int = 14,
    min_train_days: int = 180,
) -> dict[str, dict]:
    """Run stages in order, skipping those already checkpointed.

    Returns the metrics of every completed stage, whether loaded from the
    checkpoint or freshly computed. ``strict`` is passed through to
    ``stage_ingest`` (see its docstring for the subset-run semantics);
    ``n_folds``/``test_days``/``advance_days`` configure the backtest stage.
    """
    set_global_seed(seed)
    order = [
        "ingest",
        "baseline",
        "features",
        "fallbacks",
        "lightgbm",
        "catboost",
        "conformal",
        "policy",
        "shap",
        "sensitivity",
        "backtest",
    ]
    # Default run covers everything EXCEPT the sensitivity stages: each refits
    # the primary model many times, so they are opt-in (CLI --with-sensitivity
    # and --with-backtest), not part of a standard run.
    OPT_IN_STAGES = {"sensitivity", "backtest"}
    todo = stages if stages is not None else [s for s in order if s not in OPT_IN_STAGES]
    results: dict[str, dict] = {}

    for stage in order:
        if stage not in todo:
            continue
        if not force and stage in store.completed_stages():
            cached = store.stage_info(stage)
            results[stage] = cached.get("metrics", {}) if cached else {}
            print(f"[skip] {stage} (checkpointed) -> {results[stage]}")
            continue

        print(f"[run ] {stage} ...")
        started = time.time()
        if stage == "ingest":
            metrics = stage_ingest(store, data_root, seed, strict=strict)
        elif stage == "baseline":
            metrics = stage_baseline(store, seed, test_start)
        elif stage == "features":
            metrics = stage_features(store, seed, test_start)
        elif stage == "fallbacks":
            metrics = stage_fallbacks(store, seed, test_start)
        elif stage == "lightgbm":
            metrics = stage_lightgbm(store, seed, test_start)
        elif stage == "catboost":
            metrics = stage_catboost(store, seed, test_start)
        elif stage == "conformal":
            metrics = stage_conformal(store, seed, test_start)
        elif stage == "policy":
            metrics = stage_policy(store, seed, test_start)
        elif stage == "shap":
            metrics = stage_shap(store, seed, test_start)
        elif stage == "sensitivity":
            metrics = stage_sensitivity(store, seed, test_start)
        elif stage == "backtest":
            metrics = stage_backtest(
                store,
                seed,
                test_start,
                n_folds=n_folds,
                test_days=test_days,
                advance_days=advance_days,
                min_train_days=min_train_days,
            )
        else:  # pragma: no cover - guarded by the order list
            raise KeyError(stage)
        store.record_stage(
            stage, started, seed, data={"data_root": str(data_root)}, metrics=metrics
        )
        results[stage] = metrics
        print(f"[done] {stage} in {time.time() - started:.1f}s -> {metrics}")

    return results


# -------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="diq-train",
        description="DemandIQ training pipeline with resumable checkpoints.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_audit = sub.add_parser("audit", help="Run the Section 3.7.1 audit and exit")
    p_audit.add_argument("--data-root", required=True, help="Folder with the Recruit CSVs")

    p_train = sub.add_parser("train", help="Full pipeline: ingest, baseline, features, lightgbm")
    p_train.add_argument("--data-root", required=True)
    p_train.add_argument(
        "--skip-lightgbm",
        action="store_true",
        help="Stop after the features stage (implies no conformal/policy)",
    )
    p_train.add_argument(
        "--with-sensitivity",
        action="store_true",
        help="Run the Section 3.25 sensitivity stage after shap: "
        "leave-one-group-out feature ablation + the no-reservations variant",
    )
    p_train.add_argument(
        "--with-backtest",
        action="store_true",
        help="Run the rolling-origin backtest stage after shap "
        "(6 folds x 14-day advance per EVALUATION.md)",
    )
    p_train.add_argument("--backtest-folds", type=int, default=6)
    p_train.add_argument("--backtest-test-days", type=int, default=30)
    p_train.add_argument("--backtest-advance-days", type=int, default=14)
    p_train.add_argument(
        "--backtest-min-train-days",
        type=int,
        default=180,
        help="Drop folds whose train window is shorter than this",
    )
    p_train.add_argument(
        "--checkpoints", default="artifacts/runs/latest", help="Checkpoint directory"
    )
    p_train.add_argument(
        "--test-start", default="2017-02-01", help="First day of the held-out test window"
    )
    p_train.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p_train.add_argument("--force", action="store_true", help="Rerun stages already checkpointed")
    p_train.add_argument(
        "--no-strict",
        action="store_true",
        help="Subset run: block only on hard integrity failures, "
        "report scale/span deviations without failing",
    )

    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.command == "audit":
        try:
            report = audit_recruit(load_recruit(Path(args.data_root)))
        except FileNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(report.summary())
        return 0 if report.ok else 1

    stages = ["ingest", "baseline", "features", "fallbacks"]
    if not getattr(args, "skip_lightgbm", False):
        stages += ["lightgbm", "catboost", "conformal", "policy", "shap"]
    if getattr(args, "with_sensitivity", False):
        stages += ["sensitivity"]
    if getattr(args, "with_backtest", False):
        stages += ["backtest"]
    results = run_pipeline(
        store=CheckpointStore(args.checkpoints),
        data_root=Path(args.data_root),
        test_start=args.test_start,
        seed=args.seed,
        stages=stages,
        force=args.force,
        strict=not args.no_strict,
        n_folds=args.backtest_folds,
        test_days=args.backtest_test_days,
        advance_days=args.backtest_advance_days,
        min_train_days=args.backtest_min_train_days,
    )
    print("\nCompleted stages:", ", ".join(results) or "none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
