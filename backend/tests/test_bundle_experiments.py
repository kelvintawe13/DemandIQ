"""Bundle-backed experiment registration (Experiments screen provenance).

``ensure_bundle_experiments`` publishes a training run's own rolling-origin
backtest into ``experiments``/``experiment_folds`` so the console shows the
folds the run actually scored. ``ensure_bundle_tuning`` does the same for the
bounded validation-tail search: one experiment per trial, with its validation
MAE against the default configuration's. These tests pin the behaviours that
matter: registration, idempotency, and no-op without a bundle (the CI/dev
default).
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

from app.models.experiment import Experiment, ExperimentFold
from app.services.forecasting import loader

WORKSPACE = uuid.uuid4()


def _bundle() -> dict:
    return {
        "model": {"algorithm": "lightgbm", "version": "lightgbm-fs-2026.09.2-it200"},
        "metrics": {"feature_set_version": "fs-2026.09.2", "baseline_mae": 7.67},
        "backtest": {
            "n_folds": 3,
            "algorithm": "lightgbm",
            "model_version": "lightgbm-fs-2026.09.2-it200",
            "feature_set_version": "fs-2026.09.2",
            "baseline_name": "weekday_mean",
            "baseline_mae": 7.67,
            "mae_mean": 6.667,
            "mae_std": 0.341,
            "coverage_mean": 0.905,
            "folds": [
                {
                    "fold_index": 0,
                    "train_start": "2016-01-02",
                    "train_end": "2017-03-24",
                    "test_start": "2017-03-24",
                    "test_end": "2017-04-23",
                    "mae": 6.884,
                    "rmse": 11.802,
                    "coverage": 0.91,
                },
                {
                    "fold_index": 1,
                    "train_start": "2016-01-02",
                    "train_end": "2017-03-10",
                    "test_start": "2017-03-10",
                    "test_end": "2017-04-09",
                    "mae": 7.101,
                    "rmse": 11.296,
                    "coverage": 0.90,
                },
                {
                    "fold_index": 2,
                    "train_start": "2016-01-02",
                    "train_end": "2017-02-24",
                    "test_start": "2017-02-24",
                    "test_end": "2017-03-26",
                    "mae": 6.893,
                    "rmse": 12.323,
                    "coverage": 0.905,
                },
            ],
        },
    }


def _mock_db(
    existing_experiment: Experiment | None, existing_folds: list[ExperimentFold]
) -> MagicMock:
    db = MagicMock()
    db.scalar.return_value = existing_experiment
    db.scalars.return_value.all.return_value = existing_folds
    return db


def test_registers_backtest_as_experiment_with_folds(monkeypatch) -> None:
    monkeypatch.setattr(loader, "get_bundle", lambda: _bundle())
    db = _mock_db(None, [])

    loader.ensure_bundle_experiments(db, WORKSPACE)

    added = [call.args[0] for call in db.add.call_args_list]
    experiments = [o for o in added if isinstance(o, Experiment)]
    folds = [o for o in added if isinstance(o, ExperimentFold)]

    assert len(experiments) == 1
    exp = experiments[0]
    assert exp.workspace_id == WORKSPACE
    assert exp.status == "completed"
    assert "lightgbm-fs-2026.09.2-it200" in exp.name
    assert float(exp.mae) == 6.667
    assert float(exp.baseline_mae) == 7.67
    # The bundle carries a 0-1 fraction; the column stores a 0-100 percent.
    assert float(exp.coverage) == 90.5
    assert exp.config["source"] == "training_run_backtest"
    assert exp.config["folds"] == 3

    assert len(folds) == 3
    assert [f.fold_index for f in folds] == [0, 1, 2]
    assert all(f.experiment_id == exp.id for f in folds)
    assert str(folds[0].test_start) == "2017-03-24"
    assert float(folds[0].mae) == 6.884
    assert float(folds[0].coverage) == 91.0


def test_idempotent_when_experiment_and_folds_exist(monkeypatch) -> None:
    monkeypatch.setattr(loader, "get_bundle", lambda: _bundle())
    exp = Experiment(
        id=uuid.uuid4(),
        workspace_id=WORKSPACE,
        name="Rolling-origin backtest — lightgbm-fs-2026.09.2-it200",
        config={},
        status="completed",
        mae=6.667,
        baseline_mae=7.67,
    )
    existing = [
        ExperimentFold(id=uuid.uuid4(), experiment_id=exp.id, fold_index=i) for i in range(3)
    ]
    db = _mock_db(exp, existing)

    loader.ensure_bundle_experiments(db, WORKSPACE)

    assert db.add.call_count == 0


def test_noop_without_bundle(monkeypatch) -> None:
    monkeypatch.setattr(loader, "get_bundle", lambda: None)
    db = _mock_db(None, [])

    loader.ensure_bundle_experiments(db, WORKSPACE)

    db.add.assert_not_called()


def test_noop_when_run_has_no_backtest(monkeypatch) -> None:
    monkeypatch.setattr(loader, "get_bundle", lambda: {"model": {"version": "x"}})
    db = _mock_db(None, [])

    loader.ensure_bundle_experiments(db, WORKSPACE)

    db.add.assert_not_called()


# ---------------------------------------------------------------------------
# Bounded validation-tail tuning trials
# ---------------------------------------------------------------------------


def _tuning_bundle() -> dict:
    return {
        "model": {"algorithm": "lightgbm", "version": "lightgbm-fs-2026.09.2-it200"},
        "tuning": {
            "model_version": "lightgbm-fs-2026.09.2-it200",
            "feature_set_version": "fs-2026.09.2",
            "selection_split": "validation_tail",
            "selection_metric": "validation_mae",
            "max_trials": 3,
            "best_trial": 1,
            "best_params": {
                "learning_rate": 0.03,
                "num_leaves": 31,
                "min_data_in_leaf": 20,
                "feature_fraction": 0.9,
            },
            "default_validation_mae": 8.1,
            "best_validation_mae": 7.9,
            "improvement": 0.2,
            "trials": [
                {
                    "trial": 0,
                    "params": {"learning_rate": 0.05, "num_leaves": 63},
                    "validation_mae": 8.1,
                    "validation_rmse": 14.2,
                    "validation_rmsle": 0.71,
                    "validation_mase": 0.92,
                    "best_iteration": 180,
                    "selected": False,
                },
                {
                    "trial": 1,
                    "params": {"learning_rate": 0.03, "num_leaves": 31},
                    "validation_mae": 7.9,
                    "validation_rmse": 13.9,
                    "validation_rmsle": 0.69,
                    "validation_mase": 0.90,
                    "best_iteration": 240,
                    "selected": True,
                },
                {
                    "trial": 2,
                    "params": {"learning_rate": 0.03, "num_leaves": 63},
                    "validation_mae": 8.05,
                    "validation_rmse": 14.0,
                    "validation_rmsle": 0.70,
                    "validation_mase": 0.91,
                    "best_iteration": 210,
                    "selected": False,
                },
            ],
        },
    }


def test_registers_every_tuning_trial_with_its_validation_mae(monkeypatch) -> None:
    monkeypatch.setattr(loader, "get_bundle", lambda: _tuning_bundle())
    db = _mock_db(None, [])

    loader.ensure_bundle_tuning(db, WORKSPACE)

    added = [call.args[0] for call in db.add.call_args_list]
    experiments = [o for o in added if isinstance(o, Experiment)]
    assert len(experiments) == 3
    assert [e.config["trial"] for e in experiments] == [0, 1, 2]
    assert [e.config["selected"] for e in experiments] == [False, True, False]
    assert all(e.config["source"] == "training_run_tuning" for e in experiments)
    assert all(e.config["selection_split"] == "validation_tail" for e in experiments)
    assert experiments[1].config["params"] == {
        "learning_rate": 0.03,
        "num_leaves": 31,
    }
    # ``mae`` is the trial's validation-tail error; ``baseline_mae`` is the
    # documented default on the same tail, so the comparison is like-for-like.
    assert [float(e.mae) for e in experiments] == [8.1, 7.9, 8.05]
    assert all(float(e.baseline_mae) == 8.1 for e in experiments)
    # Tuning scores point accuracy only - no interval was measured.
    assert all(e.coverage is None for e in experiments)
    assert all(e.status == "completed" for e in experiments)
    assert "trial 2" in experiments[1].name


def test_tuning_registration_is_idempotent(monkeypatch) -> None:
    monkeypatch.setattr(loader, "get_bundle", lambda: _tuning_bundle())
    exp = Experiment(
        id=uuid.uuid4(),
        workspace_id=WORKSPACE,
        name="Validation-tail tuning — lightgbm-fs-2026.09.2-it200 — trial 1",
        config={"source": "training_run_tuning"},
        status="completed",
        mae=None,
        baseline_mae=None,
    )
    db = _mock_db(exp, [])

    loader.ensure_bundle_tuning(db, WORKSPACE)

    db.add.assert_not_called()
    # Missing figures are backfilled; the row is not duplicated or replaced.
    assert float(exp.mae) == 8.1
    assert float(exp.baseline_mae) == 8.1


def test_tuning_is_noop_without_a_tuning_section(monkeypatch) -> None:
    monkeypatch.setattr(loader, "get_bundle", lambda: {"model": {"version": "x"}})
    db = _mock_db(None, [])

    loader.ensure_bundle_tuning(db, WORKSPACE)

    db.add.assert_not_called()
