"""Model loader and point prediction stub (ARCHITECTURE.md §3).

Responsibilities:
* Load the production ``model_versions`` row for a workspace, publishing the
  metrics the serving bundle carries (MAE/RMSE/RMSLE, evaluation windows,
  hyperparameters) so the registry never shows a trained run as blank.
* Register the run's other candidates too (Champion + challengers), so the
  registry lists e.g. the CatBoost fallback primary next to LightGBM with the
  MAE delta the training run measured.
* Return a dummy artifact when no production version exists (so that the API
  remains operational in dev / CI).
* Handle the historical-average fallback when a location has fewer than 30
  days of history — flagged ``fallback: true`` per the spec.
* Otherwise produce a deterministic point forecast stub from the feature
  vector (roll means / same-weekday mean) so that intervals and policies can
  be exercised without a real ML artifact.
"""

from __future__ import annotations

import datetime
import logging
import statistics
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.experiment import Experiment, ExperimentFold
from app.models.forecast import ModelVersion
from app.models.location import DemandObservation
from app.services.feature_build import build_features, build_model_features
from app.services.forecasting.bundle import bundle_holidays, get_bundle

logger = logging.getLogger(__name__)

# The registry row that records "this forecast did not come from a model".
# Kept out of the ``production`` status deliberately: a workspace with a trained
# model must not have a degraded forecast attributed to it, and two rows sharing
# ``status='production'`` would make "which model is production?" a matter of
# ordering. ``ensure_bundle_models`` owns the real production row.
STUB_MODEL_VERSION = "v0-fallback"
STUB_MODEL_ALGORITHM = "historical_avg"


@dataclass(frozen=True, slots=True)
class DummyArtifact:
    """Placeholder for a loaded model artifact."""

    model_version_id: uuid.UUID
    algorithm: str
    feature_set_version: str


@dataclass(frozen=True, slots=True)
class PointResult:
    """Output of :func:`predict_point`."""

    point: float
    fallback: bool
    model_version: ModelVersion
    artifact: DummyArtifact
    history_days: int
    feature_set_version: str


def _date_or_none(value: object) -> datetime.date | None:
    """Parse an ISO date from the bundle, tolerating anything unexpected."""
    if value is None:
        return None
    try:
        return datetime.date.fromisoformat(str(value))
    except ValueError:
        return None


def _float_or_none(value: object) -> float | None:
    """Coerce a bundle number to ``float``, or ``None`` when absent/unusable."""
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _percent_or_none(value: object) -> float | None:
    """A 0-1 coverage fraction as a 0-100 percent.

    The serving bundle records coverage as a fraction (``0.901``); the
    ``experiments``/``experiment_folds`` ``coverage`` columns and the console
    read and display a percentage, so the conversion lives at this boundary.
    """
    fraction = _float_or_none(value)
    if fraction is None:
        return None
    return round(fraction * 100.0, 2)


def _apply_metrics(row: ModelVersion, metrics: object) -> bool:
    """Publish a training run's measured figures onto the registry row.

    ``metrics`` is a bundle metrics section, written by
    ``model/scripts/export_serving_bundle.py`` from the run manifest (the
    champion's under ``metrics``, a challenger's under ``comparison.<name>``).
    Every field is applied independently and only when the column is still
    empty, so older bundles and partially-filled rows are both safe and no
    number is ever invented. Returns ``True`` when anything was written.
    """
    if not isinstance(metrics, dict):
        return False

    changed = False
    for column in ("mae", "rmse", "rmsle"):
        value = metrics.get(column)
        if value is None or getattr(row, column) is not None:
            continue
        setattr(row, column, float(value))
        changed = True

    for column in ("train_start", "train_end", "test_start", "test_end"):
        if getattr(row, column) is not None:
            continue
        parsed = _date_or_none(metrics.get(column))
        if parsed is None:
            continue
        setattr(row, column, parsed)
        changed = True

    if row.hyperparameters is None and isinstance(metrics.get("hyperparameters"), dict):
        row.hyperparameters = metrics["hyperparameters"]
        changed = True

    return changed


# Evaluation detail with no ``model_versions`` column: published to the
# registry straight from the bundle (see :func:`bundle_evaluation`).
_EVALUATION_KEYS = (
    "mase",
    "bias",
    # Supplementary only (Section 3.15): shown beside the error metrics, never
    # used to rank or promote a version.
    "r2",
    "baseline_name",
    "baseline_mae",
    "baseline_rmsle",
    "mae_lift_vs_baseline",
    "mae_delta_vs_production",
    "test_rows",
    "best_iteration",
)


def _as_metrics(value: object) -> dict[str, Any]:
    """A bundle metrics section as a plain dict (empty when absent/malformed)."""
    return value if isinstance(value, dict) else {}


def bundle_model_specs(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """Every model version a training run produced, as registry-row specs.

    The champion comes from the bundle's ``model`` section (status
    ``production``); each entry of ``comparison`` is a challenger the run also
    trained and scored on the same split (status defaults to ``candidate``, so
    promotion stays a governance decision - MODEL_LIFECYCLE.md section 8).
    """
    specs: list[dict[str, Any]] = []
    champion_fs = "unknown"
    model = bundle.get("model")
    if isinstance(model, dict) and model.get("version"):
        metrics = _as_metrics(bundle.get("metrics"))
        champion_fs = str(
            metrics.get("feature_set_version") or model.get("feature_set_version") or "unknown"
        )
        specs.append(
            {
                "algorithm": model.get("algorithm") or "lightgbm",
                "version": model["version"],
                "status": "production",
                "role": "champion",
                "feature_set_version": champion_fs,
                "metrics": metrics,
            }
        )
    comparison = bundle.get("comparison")
    if isinstance(comparison, dict):
        for name, entry in comparison.items():
            if not isinstance(entry, dict):
                continue
            version = entry.get("version")
            if not version:
                continue
            metrics = _as_metrics(entry.get("metrics"))
            specs.append(
                {
                    "algorithm": entry.get("algorithm") or name,
                    "version": version,
                    "status": entry.get("status") or "candidate",
                    "role": entry.get("role") or "challenger",
                    "feature_set_version": str(metrics.get("feature_set_version") or champion_fs),
                    "metrics": metrics,
                }
            )
    return specs


def bundle_evaluation(model_version: ModelVersion) -> dict[str, Any] | None:
    """Extra evaluation detail for a registry row, read from the bundle.

    MASE/Bias, the comparison baseline and the challenger's MAE delta have no
    column on ``model_versions``, so they travel with the bundle. They are
    returned only for the spec of EXACTLY this model version, so a stale or
    unrelated export can never annotate the wrong row; ``None`` means "no
    detail available", never a guessed number.
    """
    bundle = get_bundle()
    if bundle is None:
        return None
    for spec in bundle_model_specs(bundle):
        if spec["version"] != model_version.version:
            continue
        metrics = spec.get("metrics")
        if not isinstance(metrics, dict):
            return None
        detail = {k: metrics[k] for k in _EVALUATION_KEYS if metrics.get(k) is not None}
        if spec.get("role"):
            detail["role"] = spec["role"]
        return detail or None
    return None


def bundle_calibration(model_version: ModelVersion) -> dict[str, Any] | None:
    """Interval calibration the bundle measured for THIS version, or ``None``.

    The conformal state belongs to the champion — it is the model actually run
    through the online ACI loop — so only the champion's row is annotated.
    Coverage/width come from the training run's ``conformal`` section
    (``achieved_coverage`` / ``achieved_width``); nothing is invented, and
    ``None`` means "no bundle evidence" rather than a guessed figure.
    """
    bundle = get_bundle()
    if bundle is None:
        return None
    spec = next(
        (s for s in bundle_model_specs(bundle) if s["version"] == model_version.version), None
    )
    if spec is None or spec.get("role") != "champion":
        return None
    conf = bundle.get("conformal")
    if not isinstance(conf, dict) or conf.get("target") is None:
        return None
    target = float(conf["target"]) * 100.0
    cov = conf.get("achieved_coverage")
    width = conf.get("achieved_width")
    payload: dict[str, Any] = {
        "method": "adaptive_conformal_mondrian",
        "target_coverage": target,
        "empirical_coverage": float(cov) * 100.0 if cov is not None else None,
        "mean_interval_width": float(width) if width is not None else None,
        "bins": conf.get("bins"),
        "window": conf.get("window"),
        "gamma": conf.get("gamma"),
    }
    # Section 3.16 sensitivity at other coverage levels, when the training run
    # recorded it. Left out entirely rather than stubbed, so the client can tell
    # "not measured" from "measured and empty".
    sens = conf.get("coverage_sensitivity")
    if isinstance(sens, dict) and sens:
        payload["coverage_sensitivity"] = {
            str(level): {
                "target_coverage": (
                    float(block["target_coverage"]) * 100.0
                    if block.get("target_coverage") is not None
                    else float(level) * 100.0
                ),
                "empirical_coverage": (
                    float(block["empirical_coverage"]) * 100.0
                    if block.get("empirical_coverage") is not None
                    else None
                ),
                "mean_interval_width": (
                    float(block["mean_interval_width"])
                    if block.get("mean_interval_width") is not None
                    else None
                ),
            }
            for level, block in sens.items()
            if isinstance(block, dict)
        }
    return payload


def bundle_feature_importance(model_version: ModelVersion) -> list[dict[str, Any]] | None:
    """Gain-ranked feature importance from the bundle's champion booster.

    Computed from the exported LightGBM booster (``importance_type='gain'``),
    so it reflects the trained model rather than a static list. Returns ``None``
    for a version the bundle does not describe, or a challenger that carries no
    booster; callers then fall back to their deterministic ordering.
    """
    bundle = get_bundle()
    if bundle is None:
        return None
    spec = next(
        (s for s in bundle_model_specs(bundle) if s["version"] == model_version.version), None
    )
    model = bundle.get("model")
    if spec is None or spec.get("role") != "champion" or not isinstance(model, dict):
        return None
    booster = model.get("booster")
    columns = model.get("feature_columns")
    if booster is None or not isinstance(columns, list):
        return None
    try:
        gains = booster.feature_importance(importance_type="gain")
    except Exception:  # noqa: BLE001 - a bad artifact must degrade, not 500
        return None
    pairs = [(str(c), float(g)) for c, g in zip(columns, gains)]
    total = sum(g for _, g in pairs)
    if total <= 0:
        return None
    pairs.sort(key=lambda kv: kv[1], reverse=True)
    return [
        {"feature": name, "importance": round(gain / total, 6), "rank": i + 1}
        for i, (name, gain) in enumerate(pairs)
    ]


def ensure_bundle_models(db: Session, workspace_id: uuid.UUID) -> None:
    """Register every version the loaded bundle describes (idempotent).

    Called on the paths that look a model up so the registry lists the whole
    run, not just the champion: the production model plus the challengers it
    was compared against (e.g. CatBoost). Existing rows are only backfilled
    with missing metrics - never duplicated, never overwritten - and without a
    bundle this is a no-op.
    """
    bundle = get_bundle()
    if bundle is None:
        return
    now = datetime.datetime.now(datetime.UTC)
    for spec in bundle_model_specs(bundle):
        row: ModelVersion | None = db.scalar(
            select(ModelVersion)
            .where(
                ModelVersion.workspace_id == workspace_id,
                ModelVersion.version == spec["version"],
            )
            .limit(1)
        )
        if row is None:
            is_production = spec["status"] == "production"
            row = ModelVersion(
                id=uuid.uuid4(),
                workspace_id=workspace_id,
                algorithm=spec["algorithm"],
                version=spec["version"],
                status=spec["status"],
                feature_set_version=spec["feature_set_version"],
                artifact_uri=bundle.get("run_dir"),
                trained_at=now,
                promoted_at=now if is_production else None,
            )
            db.add(row)
        _apply_metrics(row, spec.get("metrics"))
    db.flush()


def ensure_bundle_experiments(db: Session, workspace_id: uuid.UUID) -> None:
    """Register the run's rolling-origin backtest as an experiment (idempotent).

    The Experiments screen is the model-facing view of evaluation, so it lists
    the folds the training run actually scored — read from the serving bundle's
    ``backtest`` section, exactly as the registry reads its model rows (the
    backend cannot import the training package, so the run's own artifacts are
    the only honest source). Without a bundle, or for a run with no backtest,
    this is a no-op. The experiment is matched by name and its folds by index,
    so re-running never duplicates or rewrites that history.

    Args:
        db: Database session.
        workspace_id: Workspace to register the backtest for.
    """
    bundle = get_bundle()
    if bundle is None:
        return
    backtest = bundle.get("backtest")
    if not isinstance(backtest, dict):
        return
    folds = backtest.get("folds")
    if not isinstance(folds, list) or not folds:
        return

    model_version = str(backtest.get("model_version") or "unknown")
    name = f"Rolling-origin backtest — {model_version}"
    now = datetime.datetime.now(datetime.UTC)

    exp: Experiment | None = db.scalar(
        select(Experiment)
        .where(Experiment.workspace_id == workspace_id, Experiment.name == name)
        .limit(1)
    )
    if exp is None:
        exp = Experiment(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            name=name,
            hypothesis=(
                "The production configuration generalises across every expanding "
                "window: each window trains on the past and tests strictly forward."
            ),
            config={
                "algorithm": backtest.get("algorithm"),
                "feature_set": backtest.get("feature_set_version"),
                "folds": backtest.get("n_folds"),
                "source": "training_run_backtest",
                "model_version": model_version,
            },
            status="completed",
            mae=_float_or_none(backtest.get("mae_mean")),
            baseline_mae=_float_or_none(backtest.get("baseline_mae")),
            coverage=_percent_or_none(backtest.get("coverage_mean")),
            created_by=None,
            finished_at=now,
            created_at=now,
        )
        db.add(exp)
        db.flush()
    else:
        # Backfill figures an earlier bundle left blank; never overwrite a
        # number already published.
        if exp.mae is None:
            exp.mae = _float_or_none(backtest.get("mae_mean"))
        if exp.baseline_mae is None:
            exp.baseline_mae = _float_or_none(backtest.get("baseline_mae"))
        if exp.coverage is None:
            exp.coverage = _percent_or_none(backtest.get("coverage_mean"))

    existing_folds = {
        int(f.fold_index): f
        for f in db.scalars(
            select(ExperimentFold).where(ExperimentFold.experiment_id == exp.id)
        ).all()
    }
    for fold in folds:
        if not isinstance(fold, dict):
            continue
        idx = int(fold.get("fold_index") or 0)
        coverage = _percent_or_none(fold.get("coverage"))
        current = existing_folds.get(idx)
        if current is not None:
            # Backfill coverage an older bundle lacked; never overwrite.
            if current.coverage is None and coverage is not None:
                current.coverage = coverage
            continue
        db.add(
            ExperimentFold(
                id=uuid.uuid4(),
                experiment_id=exp.id,
                fold_index=idx,
                train_start=_date_or_none(fold.get("train_start")),
                train_end=_date_or_none(fold.get("train_end")),
                test_start=_date_or_none(fold.get("test_start")),
                test_end=_date_or_none(fold.get("test_end")),
                mae=_float_or_none(fold.get("mae")),
                coverage=coverage,
            )
        )
    db.flush()


def get_stub_model(db: Session, workspace_id: uuid.UUID) -> ModelVersion:
    """Find-or-create the row that records a forecast no model produced.

    The degraded paths (:func:`predict_point` with too little history, or a
    bundle that is absent or cannot honour the feature contract) return a
    historical average. That average is not the work of the workspace's trained
    model, so it is filed against this row instead — otherwise
    ``forecasts.model_version_id`` credits a LightGBM version with a rolling
    mean, and the registry reports an attribution that never happened.

    Args:
        db: Database session.
        workspace_id: Owning workspace.

    Returns:
        The persisted fallback :class:`ModelVersion`.
    """
    existing: ModelVersion | None = db.scalar(
        select(ModelVersion)
        .where(
            ModelVersion.workspace_id == workspace_id,
            ModelVersion.version == STUB_MODEL_VERSION,
        )
        .limit(1)
    )
    if existing is not None:
        return existing
    stub = ModelVersion(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        algorithm=STUB_MODEL_ALGORITHM,
        version=STUB_MODEL_VERSION,
        status="fallback",
        feature_set_version="v1",
        artifact_uri=None,
        trained_at=datetime.datetime.now(datetime.UTC),
        promoted_at=None,
    )
    db.add(stub)
    db.flush()
    return stub


def get_production_model(db: Session, workspace_id: uuid.UUID) -> ModelVersion:
    """Load the production model version for a workspace.

    When a serving bundle is configured, every version that run produced is
    registered first (:func:`ensure_bundle_models`) — the champion as
    ``production`` with its MAE/RMSE/RMSLE, evaluation windows and
    hyperparameters, plus the challengers it beat or lost to. Rows created
    before the bundle carried metrics are backfilled on the same pass.

    With no production row (no bundle, or nothing promoted yet) this returns the
    fallback row from :func:`get_stub_model`, so callers always obtain a valid
    ``model_version_id`` for persistence. Callers that are about to serve an
    actual model output want the *production* row; :func:`predict_point` uses
    :func:`get_stub_model` directly on the degraded paths.

    Args:
        db: Database session.
        workspace_id: Owning workspace.

    Returns:
        Production :class:`ModelVersion`, or the fallback row (persisted).
    """
    ensure_bundle_models(db, workspace_id)
    row: ModelVersion | None = db.scalar(
        select(ModelVersion)
        .where(
            ModelVersion.workspace_id == workspace_id,
            ModelVersion.status == "production",
            # Belt and braces with migration 012: a database that has not run
            # it still carries the fallback row as ``production``, and it must
            # never be mistaken for the model that produces forecasts.
            ModelVersion.version != STUB_MODEL_VERSION,
        )
        .order_by(ModelVersion.promoted_at.desc().nullslast(), ModelVersion.trained_at.desc().nullslast())  # type: ignore[arg-type]
        .limit(1)
    )
    if row is not None:
        return row
    return get_stub_model(db, workspace_id)


def _historical_average(
    db: Session,
    location_id: uuid.UUID,
    target_date: datetime.date,
    as_of: datetime.datetime,
) -> float | None:
    """Compute fallback historical average (mean of all history < target_date)."""
    rows = db.scalars(
        select(DemandObservation)
        .where(DemandObservation.location_id == location_id, DemandObservation.date < target_date)
        .order_by(DemandObservation.date.asc())
    ).all()
    vals = [r.demand_qty for r in rows if r.recorded_at is None or r.recorded_at <= as_of]
    if not vals:
        return None
    return float(round(statistics.mean(vals), 2))


def _point_from_bundle(bundle: dict, features: dict) -> float | None:
    """Score one feature vector with the exported LightGBM booster.

    Returns ``None`` when the serving dependencies are unavailable or the
    feature vector cannot honour the bundle's column contract — callers then
    fall back to the stub. The booster was trained on ``log1p`` demand, so
    predictions are inverted with ``expm1`` and clipped at zero, exactly as
    the training-side wrapper does.
    """
    try:
        import numpy as np
        import pandas as pd
    except ImportError:  # pragma: no cover - depends on extras
        logger.warning("numpy/pandas not installed; cannot serve the model bundle")
        return None

    m = bundle["model"]
    cols = m["feature_columns"]
    missing = [c for c in cols if c not in features]
    if missing:
        logger.warning(
            "feature vector cannot satisfy bundle contract; missing %d columns, e.g. %s",
            len(missing),
            missing[:3],
        )
        return None
    try:
        X = pd.DataFrame([{c: features[c] for c in cols}], columns=cols).astype("float64")
        raw = m["booster"].predict(X, num_iteration=m.get("best_iteration"))
        point = float(np.asarray(raw).ravel()[0])
    except Exception as exc:  # noqa: BLE001 - serving must degrade, never 500
        logger.warning("bundle prediction failed: %s", exc)
        return None
    if m.get("log_target", False):
        point = float(np.expm1(point))
    return float(round(max(0.0, point), 2))


def predict_point(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    location_id: uuid.UUID,
    target_date: datetime.date,
    as_of: datetime.datetime,
    horizon_days: int = 1,
) -> PointResult:
    """Produce a point forecast, handling the <30-day fallback.

    When a serving bundle is configured, the point comes from the exported
    LightGBM booster on the full training feature set (with the bundle's
    holiday calendar); otherwise it is derived deterministically from the
    feature vector so that tests and the UI can exercise the full pipeline
    (interval, policy, explain) without a model server.

    Args:
        db: Database session.
        workspace_id: Workspace owning the location.
        location_id: Location to forecast.
        target_date: Day being forecast.
        as_of: Forecast creation timestamp (must be ``< target_date``).
        horizon_days: Horizon (currently informational; point is for
            ``target_date`` only).

    Returns:
        :class:`PointResult` with ``fallback`` flag.

    Notes:
        ``horizon_days`` is accepted for API compatibility but not used in
        the stub beyond validation — a real implementation would loop over the
        horizon.
    """
    _ = horizon_days  # stub ignores horizon for point; batch handles horizon
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=datetime.UTC)

    model_version = get_production_model(db, workspace_id)
    feature_set_version = model_version.feature_set_version or "v1"

    # Build features — also gives history_days for the fallback decision.
    # When a serving bundle is configured, the model needs the FULL training
    # feature set AND the exact holiday calendar its training features
    # assumed; build_model_features is a superset of the stub vector.
    bundle = get_bundle()
    holidays = bundle_holidays(bundle) if bundle is not None else None
    build = build_model_features if bundle is not None else build_features
    fv = build(db, location_id=location_id, target_date=target_date, as_of=as_of, holidays=holidays)
    history_days = fv.history_days

    # Fallback if fewer than 7 days of history (was 30 — too strict, see
    # user request "who said forecasts must be after 30 days"). 7 days is the
    # minimum for roll_mean_7 (min_periods 3) and lag_7 to be meaningful;
    # below that we still show a model forecast but flag it as limited_history.
    if history_days < 7:
        avg = _historical_average(db, location_id, target_date, as_of)
        if avg is not None:
            fallback_point = float(avg)
        else:
            # No history at all — use a small neutral default so that the
            # interval remains valid and the UI has a number to show.
            fallback_point = 20.0
        # An average is not a model output, so it is filed against the fallback
        # row rather than the production model version.
        stub_version = get_stub_model(db, workspace_id)
        artifact = DummyArtifact(
            model_version_id=stub_version.id,
            algorithm=STUB_MODEL_ALGORITHM,
            feature_set_version=feature_set_version,
        )
        return PointResult(
            point=fallback_point,
            fallback=True,
            model_version=stub_version,
            artifact=artifact,
            history_days=history_days,
            feature_set_version=feature_set_version,
        )

    # Real path — serve the exported LightGBM bundle when one is configured.
    if bundle is not None:
        bundle_point = _point_from_bundle(bundle, fv.features)
        if bundle_point is not None:
            m = bundle["model"]
            artifact = DummyArtifact(
                model_version_id=model_version.id,
                algorithm=m["algorithm"],
                feature_set_version=m["feature_set_version"],
            )
            return PointResult(
                point=bundle_point,
                fallback=False,
                model_version=model_version,
                artifact=artifact,
                history_days=history_days,
                feature_set_version=m["feature_set_version"],
            )
        # Bundle present but unusable (missing deps / feature mismatch):
        # fall through to the stub so the API never hard-fails.

    # Stub path — deterministic point from feature means.
    # Prefer roll_mean_7, then roll_mean_28, then same_weekday_mean.
    point_val: float | None = fv.features.get("roll_mean_7")  # type: ignore[assignment]
    if point_val is None:
        point_val = fv.features.get("roll_mean_28")  # type: ignore[assignment]
    if point_val is None:
        point_val = fv.features.get("same_weekday_mean")  # type: ignore[assignment]
    if point_val is None:
        # Fallback to historical average even with 30+ days if no window
        avg = _historical_average(db, location_id, target_date, as_of)
        point_val = float(avg) if avg is not None else 20.0

    # Small deterministic perturbation to avoid trivial constant forecasts
    # when roll means are identical across runs (helps policy trade-off demo).
    # Uses target_date ordinal to remain reproducible per as_of_timestamp.
    jitter = (target_date.toordinal() % 5) * 0.1
    point_val = float(round(point_val + jitter, 2))
    point_val = max(0.0, point_val)

    # Attribute honestly: whatever route led here, this point is a rolling mean
    # and IS NOT the output of ``model_version`` — even when a bundle row exists
    # but could not be honoured. Filing it against the fallback row is what stops
    # the registry from crediting LightGBM with a number it did not produce.
    stub_version = get_stub_model(db, workspace_id)
    artifact2 = DummyArtifact(
        model_version_id=stub_version.id,
        algorithm=STUB_MODEL_ALGORITHM,
        feature_set_version=feature_set_version,
    )
    return PointResult(
        point=point_val,
        fallback=False,
        model_version=stub_version,
        artifact=artifact2,
        history_days=history_days,
        feature_set_version=feature_set_version,
    )
