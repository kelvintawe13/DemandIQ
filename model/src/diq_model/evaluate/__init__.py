"""Evaluation subpackage: metrics, backtesting, tables (Sections 3.11, 3.15, 3.25)."""

from diq_model.evaluate.backtest import Fold, make_folds, run_backtest
from diq_model.evaluate.metrics import all_metrics, bias, mae, mase, rmse, rmsle
from diq_model.evaluate.tables import render_markdown_table, save_table

__all__ = [
    "mae",
    "rmse",
    "rmsle",
    "mase",
    "bias",
    "all_metrics",
    "Fold",
    "make_folds",
    "run_backtest",
    "render_markdown_table",
    "save_table",
]
