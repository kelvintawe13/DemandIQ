"""Metric tests with hand-computed expected values."""

from __future__ import annotations

import numpy as np
import pytest


def test_mae_matches_manual():
    from diq_model.evaluate.metrics import mae

    assert mae([1, 2, 3], [2, 2, 2]) == pytest.approx(2 / 3)


def test_rmse_matches_manual():
    from diq_model.evaluate.metrics import rmse

    assert rmse([0, 0], [3, 4]) == pytest.approx(np.sqrt(12.5))


def test_rmsle_matches_manual():
    from diq_model.evaluate.metrics import rmsle

    # Two identical errors: (a^2 + a^2)/2 = a^2, so rmsle = |log1p(2)-log1p(1)| = ln(1.5)
    expected = np.log(1.5)
    assert rmsle([1, 1], [2, 2]) == pytest.approx(expected)


def test_rmsle_handles_zero_and_negative_predictions():
    from diq_model.evaluate.metrics import rmsle

    # Zero truth and negative prediction must not crash.
    value = rmsle([0, 5], [-1, 5])
    assert value >= 0.0


def test_all_metrics_keys():
    from diq_model.evaluate.metrics import all_metrics

    out = all_metrics([1, 2], [1, 2])
    assert set(out) == {"mae", "rmse", "rmsle"}
    assert all(v == 0.0 for v in out.values())


def test_shape_mismatch_raises():
    from diq_model.evaluate.metrics import mae

    with pytest.raises(ValueError):
        mae([1, 2], [1])


def test_bias_is_signed():
    from diq_model.evaluate.metrics import bias

    # Over-forecast -> positive; under-forecast -> negative.
    assert bias([10, 10], [12, 12]) == pytest.approx(2.0)
    assert bias([10, 10], [8, 8]) == pytest.approx(-2.0)


def test_mase_against_seasonal_naive_scale():
    from diq_model.evaluate.metrics import mase

    # Seasonal-naive on train: perfect one-step repetition -> MAE 0 would be
    # degenerate, so use a varied series with known scale.
    train = [10, 12, 14, 10, 12, 14, 10, 12, 14, 10, 12, 14]
    # m=7 scale: mean(|train[7:] - train[:-7]|) = 0 here -> degenerate.
    # Use a non-periodic series instead:
    train2 = np.array([5, 9, 2, 11, 7, 3, 12, 6, 8, 4, 10, 1, 9, 5], dtype=float)
    scale = np.mean(np.abs(train2[7:] - train2[:-7]))
    y_true = np.array([10.0, 12.0])
    y_pred = np.array([9.0, 14.0])
    expected = np.mean(np.abs(y_true - y_pred)) / scale
    assert mase(y_true, y_pred, train2) == pytest.approx(expected)


def test_mase_short_train_falls_back_to_m1():
    from diq_model.evaluate.metrics import mase

    # len(train)=3, season_length=7 -> falls back to m=1 scale.
    train = np.array([5.0, 9.0, 2.0])
    scale = np.mean(np.abs(train[1:] - train[:-1]))
    y_true = np.array([10.0])
    y_pred = np.array([8.0])
    assert mase(y_true, y_pred, train, season_length=7) == pytest.approx(2.0 / scale)


def test_mase_degenerate_constant_train_is_nan():
    from diq_model.evaluate.metrics import mase

    train = np.array([7.0, 7.0, 7.0, 7.0, 7.0, 7.0, 7.0, 7.0])
    assert np.isnan(mase([7.0], [7.0], train))


def test_all_metrics_with_train_includes_mase_and_bias():
    from diq_model.evaluate.metrics import all_metrics

    out = all_metrics([1, 2], [1, 2], y_train=[1, 2, 3, 4, 5, 6, 7, 8])
    assert {"mae", "rmse", "rmsle", "mase", "bias", "r2"} <= set(out)
    # A perfect fit on a constant-free series is exactly R^2 = 1.
    assert out["r2"] == 1.0


def test_all_metrics_without_train_keeps_original_contract():
    from diq_model.evaluate.metrics import all_metrics

    out = all_metrics([1, 2], [1, 2])
    assert set(out) == {"mae", "rmse", "rmsle"}


def test_r2_perfect_fit_is_one():
    from diq_model.evaluate.metrics import r2

    assert r2([1, 2, 3, 4], [1, 2, 3, 4]) == pytest.approx(1.0)


def test_r2_of_the_mean_prediction_is_zero():
    """Predicting the mean explains nothing: R^2 is 0 by definition."""
    from diq_model.evaluate.metrics import r2

    truth = [2.0, 4.0, 6.0, 8.0]
    assert r2(truth, [5.0] * 4) == pytest.approx(0.0)


def test_r2_is_negative_when_worse_than_the_mean():
    """Section 3.15's blunt signal: negative R^2 beats reporting nothing."""
    from diq_model.evaluate.metrics import r2

    assert r2([2.0, 4.0, 6.0, 8.0], [9.0, 9.0, 1.0, 1.0]) < 0.0


def test_r2_is_nan_when_the_truth_has_no_variance():
    """Every day identical: the ratio is undefined, not zero."""
    from diq_model.evaluate.metrics import r2

    assert np.isnan(r2([5.0, 5.0, 5.0], [4.0, 5.0, 6.0]))


# ------------------------------------------------------ NaN handling
def test_nan_truth_raises_loudly():
    from diq_model.evaluate.metrics import mae

    with pytest.raises(ValueError, match="y_true contains"):
        mae([1.0, np.nan, 3.0], [1.0, 1.0, 1.0])


def test_nan_predictions_dropped_with_warning():
    from diq_model.evaluate.metrics import mae

    with pytest.warns(RuntimeWarning, match="NaN predictions"):
        # Dropped row (3, NaN); scored rows (1,2) and (5,2): (1+3)/2 = 2.0
        assert mae([1.0, 3.0, 5.0], [2.0, np.nan, 2.0]) == pytest.approx(2.0)


def test_all_nan_predictions_raise():
    from diq_model.evaluate.metrics import mae

    with pytest.warns(RuntimeWarning, match="NaN predictions"):
        with pytest.raises(ValueError, match="entirely NaN"):
            mae([1.0, 2.0], [np.nan, np.nan])
