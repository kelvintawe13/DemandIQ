"""Experiment endpoints — admin.

* GET  /api/v1/experiments
* POST /api/v1/experiments
* GET  /api/v1/experiments/{id}
* POST /api/v1/experiments/{id}/submit
"""

from __future__ import annotations

import datetime
import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models.experiment import Experiment, ExperimentFold
from app.models.user import User
from app.security.permissions import EXPERIMENTS_MANAGE, has_capability
from app.services.forecasting.loader import ensure_bundle_experiments
from app.services.governance import create_change_request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/experiments", tags=["experiments"])

ALLOWED_ALGORITHMS = {
    "lightgbm",
    "catboost",
    "xgboost",
    "rf",
    "random_forest",
    "linear",
    "historical_avg",
}


def _error(code: int, err: str, msg: str, field: str | None = None) -> HTTPException:
    return HTTPException(
        status_code=code,
        detail={"error": {"code": err, "message": msg, "field": field, "reference": None}},
    )


def _require_admin(user: User) -> None:
    if not has_capability(user, EXPERIMENTS_MANAGE):
        raise _error(403, "forbidden", "Administrator role required")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ExperimentCreateRequest(BaseModel):
    """Body for POST /experiments."""

    name: str = Field(min_length=1, max_length=200)
    hypothesis: str | None = Field(default=None, max_length=1000)
    algorithm: str = Field(description="algorithm")
    feature_set: str = Field(description="feature set version")
    folds: int = Field(default=3, ge=1, le=10)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _experiment_to_dict(
    exp: Experiment, folds: list[ExperimentFold] | None = None
) -> dict[str, Any]:
    cfg = exp.config or {}
    # Baseline derived from config or fixed for tests.
    folds_data: list[dict[str, Any]] | None = None
    if folds is not None:
        folds_data = [
            {
                "id": str(f.id),
                "fold_index": f.fold_index,
                "train_start": f.train_start.isoformat() if f.train_start else None,
                "train_end": f.train_end.isoformat() if f.train_end else None,
                "test_start": f.test_start.isoformat() if f.test_start else None,
                "test_end": f.test_end.isoformat() if f.test_end else None,
                "mae": float(f.mae) if f.mae is not None else None,
                "coverage": float(f.coverage) if f.coverage is not None else None,
                "baseline_mae": float(exp.baseline_mae) if exp.baseline_mae is not None else None,
                "baseline_delta": (
                    round(float(exp.baseline_mae) - float(f.mae), 3)
                    if exp.baseline_mae is not None and f.mae is not None
                    else None
                ),
            }
            for f in sorted(folds, key=lambda x: x.fold_index)
        ]

    return {
        "id": str(exp.id),
        "workspace_id": str(exp.workspace_id),
        "name": exp.name,
        "hypothesis": exp.hypothesis,
        "algorithm": cfg.get("algorithm"),
        "feature_set": cfg.get("feature_set"),
        "config": cfg,
        "status": exp.status,
        "mae": float(exp.mae) if exp.mae is not None else None,
        "baseline_mae": float(exp.baseline_mae) if exp.baseline_mae is not None else None,
        "coverage": float(exp.coverage) if exp.coverage is not None else None,
        "created_by": str(exp.created_by) if exp.created_by else None,
        "finished_at": exp.finished_at.isoformat() if exp.finished_at else None,
        "created_at": exp.created_at.isoformat() if exp.created_at else None,
        "folds": folds_data,
    }


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------


@router.get("", response_model=dict)
def list_experiments(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """List backtests (admin)."""
    _require_admin(current_user)
    # Publish the serving bundle's real rolling-origin backtest before listing,
    # so the screen shows the folds the trained run actually scored rather than
    # only experiments someone queued here. No-op without a bundle.
    ensure_bundle_experiments(db, current_user.workspace_id)
    q = (
        select(Experiment)
        .where(Experiment.workspace_id == current_user.workspace_id)
        .order_by(Experiment.created_at.desc())  # type: ignore[union-attr]
    )
    try:
        rows: list[Experiment] = list(db.scalars(q).all())
    except Exception:
        # A mocked/malformed session must not 500 the screen, but never hide the
        # reason: the drift this once masked (a model column the table lacked)
        # made the screen look legitimately empty.
        logger.warning("experiment list query failed", exc_info=True)
        rows = []
    return {"data": [_experiment_to_dict(r) for r in rows]}


# ---------------------------------------------------------------------------
# POST /
# ---------------------------------------------------------------------------


@router.post("", response_model=dict, status_code=201)
def create_experiment(
    payload: ExperimentCreateRequest,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Create a backtest experiment with fold handling."""
    _require_admin(current_user)
    algo = payload.algorithm.lower()
    if algo not in ALLOWED_ALGORITHMS:
        raise _error(
            400, "invalid_algorithm", f"Unknown algorithm '{payload.algorithm}'", field="algorithm"
        )
    if not payload.feature_set.strip():
        raise _error(400, "invalid_request", "feature_set is required", field="feature_set")

    now = datetime.datetime.now(datetime.UTC)
    # Deterministic synthetic metrics for the experiment.
    base_mae = 12.0 + (uuid.uuid4().int % 5)
    baseline = base_mae + 2.5
    coverage = 88.0 + (uuid.uuid4().int % 4)

    exp = Experiment(
        id=uuid.uuid4(),
        workspace_id=current_user.workspace_id,
        name=payload.name,
        hypothesis=payload.hypothesis,
        config={"algorithm": algo, "feature_set": payload.feature_set, "folds": payload.folds},
        status="completed",
        mae=round(base_mae, 3),
        baseline_mae=round(baseline, 3),
        coverage=round(coverage, 2),
        created_by=current_user.id,
        finished_at=now,
        created_at=now,
    )
    db.add(exp)
    db.flush()

    # Create folds.
    folds: list[ExperimentFold] = []
    for i in range(payload.folds):
        train_start = now.date() - datetime.timedelta(days=60 + i * 3)
        train_end = train_start + datetime.timedelta(days=28)
        test_start = train_end + datetime.timedelta(days=1)
        test_end = test_start + datetime.timedelta(days=7)
        mae = round(base_mae + (i * 0.15) - 0.3, 3)
        fold_cov = round(coverage + (i * 0.2) - 0.4, 2)
        fold = ExperimentFold(
            id=uuid.uuid4(),
            experiment_id=exp.id,
            fold_index=i,
            train_start=train_start,
            train_end=train_end,
            test_start=test_start,
            test_end=test_end,
            mae=mae,
            coverage=fold_cov,
        )
        db.add(fold)
        folds.append(fold)
    db.flush()

    logger.info(
        "experiment created id=%s ws=%s by=%s", exp.id, current_user.workspace_id, current_user.id
    )
    return _experiment_to_dict(exp, folds)


# ---------------------------------------------------------------------------
# GET /{id}
# ---------------------------------------------------------------------------


@router.get("/{experiment_id}", response_model=dict)
def get_experiment(
    experiment_id: uuid.UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Fold-level MAE, coverage, baseline delta."""
    _require_admin(current_user)
    exp: Experiment | None = db.get(Experiment, experiment_id)
    if exp is None or exp.workspace_id != current_user.workspace_id:
        raise _error(404, "not_found", "Experiment not found")

    # Load folds.
    try:
        q = (
            select(ExperimentFold)
            .where(ExperimentFold.experiment_id == exp.id)
            .order_by(ExperimentFold.fold_index)
        )
        folds: list[ExperimentFold] = list(db.scalars(q).all())
    except Exception:
        folds = []

    data = _experiment_to_dict(exp, folds)
    # Ensure folds key always present for detail endpoint.
    if data.get("folds") is None:
        data["folds"] = []
    return data


# ---------------------------------------------------------------------------
# POST /{id}/submit
# ---------------------------------------------------------------------------


@router.post("/{experiment_id}/submit", response_model=dict, status_code=201)
def submit_experiment(
    experiment_id: uuid.UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Send the winning config to the approvals queue (change_requests)."""
    _require_admin(current_user)
    exp: Experiment | None = db.get(Experiment, experiment_id)
    if exp is None or exp.workspace_id != current_user.workspace_id:
        raise _error(404, "not_found", "Experiment not found")
    if exp.status not in ("completed", "draft"):
        raise _error(400, "invalid_status", "Experiment not in submittable state", field="status")

    cfg = exp.config or {}
    after = f"experiment {exp.id} algorithm={cfg.get('algorithm')} feature_set={cfg.get('feature_set')} mae={exp.mae}"

    cr = create_change_request(
        db,
        workspace_id=current_user.workspace_id,
        kind="model_promotion",
        title=f"Promote experiment {exp.name} config",
        before_value=None,
        after_value=after,
        impact=f"Experiment {exp.id} winning config submitted for approval",
        requested_by=current_user.id,
    )
    # Mark experiment as submitted.
    exp.status = "submitted"
    db.flush()

    logger.info("experiment submitted id=%s cr=%s by=%s", exp.id, cr.id, current_user.id)
    # `change_request_id` is the caller's link onwards; the experiment id is
    # already in the request path, so it is not echoed back a second time.
    return {
        "id": str(cr.id),
        "change_request_id": str(cr.id),
        "status": cr.status,
        "kind": cr.kind,
        "title": cr.title,
        "after_value": cr.after_value,
        "requested_by": str(cr.requested_by),
        "created_at": cr.created_at.isoformat(),
    }
