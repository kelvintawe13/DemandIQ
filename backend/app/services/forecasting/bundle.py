"""Serving-bundle loader — the bridge between training runs and the API.

A *serving bundle* is a joblib file exported from a ``diq_model`` training
run (``model/scripts/export_serving_bundle.py``). It contains everything the
API needs to serve real forecasts without importing the training package:

* the native LightGBM booster (trained on ``log1p`` demand),
* the exact feature-column order and feature-set version,
* the final online conformal state (per-stratum alpha + trailing residual
  windows of the Mondrian ACI, plus the global fallback),
* the run's point-accuracy metrics (MAE/RMSE/RMSLE, MASE/Bias, the best
  baseline and the champion's lift over it) with the train/test evaluation
  windows and booster hyperparameters — published on the ``model_versions``
  registry row by :func:`app.services.forecasting.loader.get_production_model`,
* provenance (run directory, seed, source-data checksums).

The loader is deliberately defensive: if ``MODEL_BUNDLE_PATH`` is unset, the
file is missing/corrupt, or the optional serving dependencies (numpy, pandas,
lightgbm, joblib) are not installed, :func:`get_bundle` returns ``None`` and
the forecasting service transparently falls back to its deterministic stub.
"""

from __future__ import annotations

import datetime
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)

BUNDLE_FORMAT = "demandiq-serving-bundle"
BUNDLE_VERSION = 1


def _load_bundle_file(path: Path) -> dict[str, Any] | None:
    """Load and validate the bundle file. Returns None on any problem."""
    try:
        import joblib  # optional serving dependency
    except ImportError:  # pragma: no cover - depends on extras
        logger.warning("joblib not installed; serving bundle unavailable")
        return None
    try:
        bundle = joblib.load(path)
    except Exception as exc:  # noqa: BLE001 - any corrupt file must degrade
        logger.warning("serving bundle at %s failed to load: %s", path, exc)
        return None
    if not isinstance(bundle, dict) or bundle.get("format") != BUNDLE_FORMAT:
        logger.warning("file at %s is not a demandiq serving bundle", path)
        return None
    if int(bundle.get("bundle_version", 0)) < BUNDLE_VERSION:
        logger.warning("serving bundle version %s is too old", bundle.get("bundle_version"))
        return None
    return bundle


@lru_cache(maxsize=4)
def _cached_load(resolved_path: str, mtime_ns: int) -> dict[str, Any] | None:
    """Cache key includes mtime so re-exports are picked up without restart."""
    return _load_bundle_file(Path(resolved_path))


# Well-known locations probed when MODEL_BUNDLE_PATH is unset (or missing):
# the standard training-run export relative to the backend/ working directory,
# the docker-compose ./model:/model mount, and a repo-root relative path.
#
# The public name is the definition; `_DEFAULT_BUNDLE_CANDIDATES` is the
# attribute actually consulted, so the test suite can pin discovery off without
# reaching into private state (see tests/conftest.py).
DEFAULT_BUNDLE_CANDIDATES = (
    "../model/artifacts/runs/latest/serving_bundle.joblib",
    "/model/artifacts/runs/latest/serving_bundle.joblib",
    "model/artifacts/runs/latest/serving_bundle.joblib",
)
_DEFAULT_BUNDLE_CANDIDATES = DEFAULT_BUNDLE_CANDIDATES


def _resolve_bundle_path() -> Path | None:
    """Explicit MODEL_BUNDLE_PATH wins; otherwise probe the default run path."""
    try:
        configured = get_settings().model_bundle_path
    except Exception:  # noqa: BLE001 - serving must degrade; settings are import-safe
        return None
    if configured:
        path = Path(configured)
        if path.exists():
            return path
        # Loud on purpose: an operator who set the variable believes the API is
        # serving the trained model. Continuing quietly into the stub would make
        # the deployment look correct while every forecast is a rolling mean.
        logger.error(
            "MODEL_BUNDLE_PATH=%s does not exist; falling back to stub forecasts"
            " unless a default location has one",
            path,
        )
    for candidate in _DEFAULT_BUNDLE_CANDIDATES:
        path = Path(candidate)
        if path.exists():
            return path
    return None


def get_bundle() -> dict[str, Any] | None:
    """Return the current serving bundle, or ``None`` to use the stub.

    Resolution order: ``MODEL_BUNDLE_PATH`` (env) when set, then the standard
    training-run location (``model/artifacts/runs/latest/serving_bundle.joblib``
    relative to the backend working directory or the compose ``/model`` mount).
    Reloads automatically when the file's mtime changes (e.g. after a fresh
    ``make bundle`` export).
    """
    path = _resolve_bundle_path()
    if path is None:
        logger.warning("no serving bundle found; using stub forecasts")
        return None
    loaded = _cached_load(str(path.resolve()), path.stat().st_mtime_ns)
    if loaded is not None:
        logger.info(
            "serving bundle loaded: model=%s/%s, feature_set=%s",
            loaded.get("model", {}).get("algorithm"),
            loaded.get("model", {}).get("version"),
            loaded.get("model", {}).get("feature_set_version"),
        )
    return loaded


def clear_bundle_cache() -> None:
    """Reset the loader cache (used by tests and after re-export)."""
    _cached_load.cache_clear()


def bundle_details() -> dict[str, Any] | None:
    """The loaded export as data, for the model console's live serving panel.

    ``path`` / ``changed_at`` / ``exported_at`` describe the file that is being
    served (the loader re-reads it whenever its mtime moves, so ``changed_at``
    is the moment the current export arrived); ``algorithm`` / ``version`` /
    ``feature_set_version`` come from the bundle's own ``model`` block and are
    what must be compared with the registry's production row.

    Returns ``None`` when the stub will answer, so a caller reports a state
    rather than an empty object.
    """
    path = _resolve_bundle_path()
    if path is None:
        return None
    bundle = get_bundle()
    if bundle is None:
        return None
    model = bundle.get("model") or {}
    try:
        changed_at = datetime.datetime.fromtimestamp(path.stat().st_mtime, datetime.UTC).isoformat()
    except OSError:  # vanished between resolution and stat
        changed_at = None
    exported = bundle.get("created_at")
    return {
        "path": str(path),
        "changed_at": changed_at,
        "exported_at": str(exported) if exported else None,
        "algorithm": model.get("algorithm"),
        "version": model.get("version"),
        "feature_set_version": model.get("feature_set_version"),
    }


class ServingConfigurationError(RuntimeError):
    """``MODEL_BUNDLE_PATH`` is set but the bundle there cannot be served."""


def serving_status() -> dict[str, Any]:
    """How forecasts will be produced, as data, for boot checks and health.

    ``mode`` is one of:

    * ``"trained"`` — a serving bundle is loaded and will answer.
    * ``"stub"`` — no bundle was asked for and none was found; the documented
      deterministic stub. Legitimate, and reports why.
    * ``"error"`` — ``MODEL_BUNDLE_PATH`` was **set** and no loadable bundle is
      being served from it. This is not a stub: the operator declared that this
      deployment serves the trained model, so silently answering with rolling
      means is a configuration error, not a degradation.

    The last case is the one worth failing on. An unset path is a deliberate
    choice; a set path that does not load is a mistake that otherwise shows up
    as a healthy-looking API returning numbers that never touched the model.
    """
    configured = (get_settings().model_bundle_path or "").strip() or None
    bundle = get_bundle()
    served = _resolve_bundle_path()

    if configured:
        resolved_configured = Path(configured).expanduser().resolve()
        if bundle is None or served is None or served.resolve() != resolved_configured:
            return {
                "mode": "error",
                "configured": True,
                "path": configured,
                "reason": (
                    f"MODEL_BUNDLE_PATH={configured} is not a loadable serving bundle. "
                    "Unset it to run the stub deliberately, or point it at an exported "
                    "serving_bundle.joblib."
                ),
            }
        model = bundle.get("model") or {}
        return {
            "mode": "trained",
            "configured": True,
            "path": str(served),
            "algorithm": model.get("algorithm"),
            "version": model.get("version"),
            "feature_set_version": model.get("feature_set_version"),
            "reason": None,
        }

    if bundle is None:
        return {
            "mode": "stub",
            "configured": False,
            "path": None,
            "reason": "no serving bundle found; using the deterministic stub",
        }
    model = bundle.get("model") or {}
    return {
        "mode": "trained",
        "configured": False,
        "path": str(served) if served else None,
        "algorithm": model.get("algorithm"),
        "version": model.get("version"),
        "feature_set_version": model.get("feature_set_version"),
        "reason": None,
    }


def assert_serving_configuration() -> None:
    """Fail loudly when an explicit ``MODEL_BUNDLE_PATH`` is not loadable.

    Called at startup so a misconfigured deployment refuses to boot rather than
    serving stub forecasts behind a healthy-looking API. A set-but-broken path
    is the only case that raises: an *unset* path is the documented stub, and a
    default-discovered bundle is whatever this deployment happens to ship.

    Raises:
        ServingConfigurationError: if the configured bundle cannot be served.
    """
    status = serving_status()
    if status["mode"] == "error":
        raise ServingConfigurationError(str(status["reason"]))


def describe_serving_mode() -> list[str]:
    """One-line, human-readable summary of how forecasts will be produced.

    Called at startup so an operator can see from the boot log whether the API
    is serving the trained model or the degraded stub, without having to issue a
    forecast and inspect its provenance.
    """
    bundle = get_bundle()
    if bundle is None:
        configured = get_settings().model_bundle_path
        detail = (
            f"MODEL_BUNDLE_PATH={configured} could not be loaded"
            if configured
            else "no serving bundle found"
        )
        return [f"STUB forecasts (rolling means): {detail}"]
    model = bundle.get("model") or {}
    return [
        "trained bundle loaded — "
        f"algorithm={model.get('algorithm')} version={model.get('version')} "
        f"feature_set={model.get('feature_set_version')}"
    ]


def bundle_holidays(bundle: dict[str, Any]) -> frozenset[datetime.date] | None:
    """Exact holiday dates the bundle's feature flags were built with.

    The exported bundle carries the calendar from the training run's ingest
    (``calendar.parquet``); the training features' ``is_holiday`` /
    ``day_before_holiday`` / ``day_after_holiday`` assumed it, so serving
    must use the same set. Returns ``None`` for bundles without a calendar
    section (older exports), letting callers fall back to their locale stub.
    """
    try:
        raw = bundle["calendar"]["holiday_dates"]
        return frozenset(datetime.date.fromisoformat(s) for s in raw)
    except (KeyError, TypeError, ValueError):
        return None
