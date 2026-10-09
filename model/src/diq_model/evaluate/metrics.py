"""Evaluation metrics (Section 3.15; EVALUATION.md "Metrics").

Point accuracy:
- MAE is the headline operational metric in visitor units.
- RMSE penalises large misses.
- RMSLE is the primary comparison metric given the strongly right-skewed
  visitor distribution (mean 20.97, max 877).
- MASE is scale-free, so stores of different sizes compare fairly; the
  scale is the in-sample seasonal-naive MAE (m=7 for daily demand).
- Bias (ME) is signed: a model that is right on average but always low is
  not fine.

R^2 is reported as the supplementary statistic Section 3.15 allows, and it is
**never** a selection criterion: it is scale-blind in the wrong direction here
(the variance is dominated by the busiest stores) and a model can raise it
while getting the small sites wrong. It is included because the proposal names
it, and because a negative value is a usefully blunt signal that a model is
worse than predicting the mean.

MAPE is deliberately NOT implemented: it explodes on low-demand days and
would systematically misrepresent small sites (EVALUATION.md).
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd


def mae(y_true: pd.Series | np.ndarray, y_pred: pd.Series | np.ndarray) -> float:
    """Mean Absolute Error in visitor units."""
    y_true, y_pred = _pair(y_true, y_pred)
    return float(np.mean(np.abs(y_true - y_pred)))


def rmse(y_true: pd.Series | np.ndarray, y_pred: pd.Series | np.ndarray) -> float:
    """Root Mean Squared Error; weights large misses more heavily."""
    y_true, y_pred = _pair(y_true, y_pred)
    return float(np.sqrt(np.mean((y_true - y_pred) ** 2)))


def rmsle(y_true: pd.Series | np.ndarray, y_pred: pd.Series | np.ndarray) -> float:
    """Root Mean Squared Logarithmic Error.

    Uses log1p so zero-demand days are handled. Predictions are clipped to
    >= 0 first: a negative prediction would otherwise crash the log.
    """
    y_true, y_pred = _pair(y_true, y_pred)
    y_pred = np.clip(y_pred, 0.0, None)
    return float(np.sqrt(np.mean((np.log1p(y_pred) - np.log1p(y_true)) ** 2)))


def bias(y_true: pd.Series | np.ndarray, y_pred: pd.Series | np.ndarray) -> float:
    """Mean Error (signed): mean(y_pred - y_true).

    Positive = systematic over-forecast, negative = systematic under-forecast.
    Zero on average is not enough on its own - the sign matters.
    """
    a, b = _pair(y_true, y_pred)
    return float(np.mean(b - a))


def r2(y_true: pd.Series | np.ndarray, y_pred: pd.Series | np.ndarray) -> float:
    """Coefficient of determination on raw visitor counts (supplementary).

    ``1 - SS_res / SS_tot``, where ``SS_tot`` is measured against the mean of
    ``y_true``. Negative values are legitimate and mean the model is worse
    than predicting the mean; ``NaN`` is returned when the truth has no
    variance to explain (every day identical), because the ratio is then
    undefined rather than zero.

    Reported beside MAE/RMSE/RMSLE (Section 3.15) but never used alone to
    select a forecasting model: it is dominated by the largest stores.
    """
    a, b = _pair(y_true, y_pred)
    ss_tot = float(np.sum((a - a.mean()) ** 2))
    if ss_tot == 0.0:
        return float("nan")
    ss_res = float(np.sum((a - b) ** 2))
    return float(1.0 - ss_res / ss_tot)


def mase(
    y_true: pd.Series | np.ndarray,
    y_pred: pd.Series | np.ndarray,
    y_train: pd.Series | np.ndarray,
    season_length: int = 7,
) -> float:
    """Mean Absolute Scaled Error (Hyndman & Koehler).

    Scale = the in-sample MAE of the seasonal-naive forecast on ``y_train``
    (shift by ``season_length``, default 7 for daily demand). Falls back to
    m=1 when the training series is too short for a seasonal scale, and to
    NaN when the scale is degenerate (constant training series).
    """
    y_true, y_pred = _pair(y_true, y_pred)
    train = np.asarray(y_train, dtype="float64")
    m = season_length if len(train) > season_length else 1
    if len(train) <= m:
        return float("nan")
    scale = float(np.mean(np.abs(train[m:] - train[:-m])))
    if scale == 0.0 or np.isnan(scale):
        return float("nan")
    return float(np.mean(np.abs(y_true - y_pred)) / scale)


def all_metrics(
    y_true: pd.Series | np.ndarray,
    y_pred: pd.Series | np.ndarray,
    y_train: pd.Series | np.ndarray | None = None,
    season_length: int = 7,
) -> dict[str, float]:
    """The Section 3.15 metric set, rounded for reporting.

    Without ``y_train`` the result carries mae/rmse/rmsle (the original
    contract, kept for callers that only want point error). Pass ``y_train``
    to get the full evaluation set the stages use: mase (which needs the
    in-sample scale), bias, and the supplementary r2.
    """
    out = {
        "mae": round(mae(y_true, y_pred), 3),
        "rmse": round(rmse(y_true, y_pred), 3),
        "rmsle": round(rmsle(y_true, y_pred), 4),
    }
    if y_train is not None:
        out["mase"] = round(mase(y_true, y_pred, y_train, season_length), 4)
        out["bias"] = round(bias(y_true, y_pred), 3)
        out["r2"] = round(r2(y_true, y_pred), 4)
    return out


def _pair(
    y_true: pd.Series | np.ndarray, y_pred: pd.Series | np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Align truth/prediction and handle NaNs explicitly.

    NaN in ``y_true`` is a data bug and raises loudly. NaN in ``y_pred``
    (a model with no prediction for a row) is dropped with a warning - a
    single missing prediction must not silently poison the whole metric
    (real Recruit data has stores that opened mid-window), but the drop is
    never invisible.
    """
    a = np.asarray(y_true, dtype="float64")
    b = np.asarray(y_pred, dtype="float64")
    if a.shape != b.shape:
        raise ValueError(f"shape mismatch: y_true {a.shape} vs y_pred {b.shape}")
    if np.isnan(a).any():
        raise ValueError(
            f"y_true contains {int(np.isnan(a).sum())} NaN values - fix the data"
        )
    n_nan = int(np.isnan(b).sum())
    if n_nan:
        warnings.warn(
            f"y_pred contains {n_nan}/{len(b)} NaN predictions; "
            "metrics computed on the predicted rows only",
            RuntimeWarning,
            stacklevel=3,
        )
        keep = ~np.isnan(b)
        a, b = a[keep], b[keep]
        if len(a) == 0:
            raise ValueError("y_pred is entirely NaN - nothing to score")
    return a, b
