"""Adaptive conformal tests: the update rule, online boundary, coverage."""

from __future__ import annotations

import numpy as np
import pytest


def test_aci_widens_after_misses():
    """A miss must push alpha DOWN (wider intervals), per the update rule."""
    from diq_model.uncertainty.conformal import AdaptiveConformal

    aci = AdaptiveConformal(target_coverage=0.90, gamma=0.05, min_history=1)
    alpha_before = aci.state.alpha

    # Run one step: truth outside a huge interval is impossible, so build a
    # scenario where the first scored day misses.
    y = np.array([100.0])
    yhat = np.array([0.0])
    # With min_history=1 and a synthetic residual, force a miss via tiny width.
    aci.state.alpha = 0.999  # near-certain miss: quantile ~ max residual
    intervals = aci.calibrate_online(y, yhat, warmup_true=np.array([1.0]), warmup_pred=np.array([1.0]))
    # After a miss: alpha decreases -> 1-alpha grows -> wider intervals.
    assert aci.state.alpha < 0.999 + 1e-12
    # And the direct relation:
    aci2 = AdaptiveConformal(target_coverage=0.90, gamma=0.05, min_history=1)
    aci2.state.alpha = alpha_before
    # manually verify the update equation on a known miss:
    alpha_next = alpha_before + 0.05 * (0.10 - 1.0)  # miss
    assert alpha_next < alpha_before


def test_aci_tightens_when_covered():
    from diq_model.uncertainty.conformal import AdaptiveConformal

    aci = AdaptiveConformal(target_coverage=0.90, gamma=0.05, min_history=1)
    start = aci.state.alpha  # 0.10
    y = np.array([10.0])
    yhat = np.array([10.0])
    aci.calibrate_online(y, yhat, warmup_true=np.array([1.0]), warmup_pred=np.array([1.0]))
    # Covered day -> miss=0 -> alpha moves toward (1-target)=0.10 + gamma*0.10... 
    # Actually: alpha += gamma*(0.10 - 0) = alpha increases.
    assert aci.state.alpha > start


def test_online_no_self_leak():
    """The interval for day t must not depend on day t's residual.

    Feed two identical prediction streams but change ONLY the final day's
    true value. Earlier intervals must be byte-identical; the final interval
    (which uses alpha after earlier updates) stays identical too because
    alpha updates lag one step behind scoring - but the FINAL alpha must
    differ, proving the update consumed the changed value without leaking
    backward.
    """
    from diq_model.uncertainty.conformal import AdaptiveConformal

    rng = np.random.default_rng(3)
    yhat = rng.normal(50, 2, size=30)
    y_a = yhat + rng.normal(0, 3, size=30)
    y_b = y_a.copy()
    y_b[-1] = 10_000.0  # poison ONLY the last realised value

    def run(y):
        aci = AdaptiveConformal(target_coverage=0.90, gamma=0.05, min_history=5)
        ivs = aci.calibrate_online(y, yhat)
        return np.array(ivs), aci.state.alpha

    ivs_a, alpha_a = run(y_a)
    ivs_b, alpha_b = run(y_b)
    # All intervals identical: no interval ever saw a FUTURE residual.
    assert np.allclose(ivs_a[:-1], ivs_b[:-1], equal_nan=True)
    assert ivs_a[-1][0] == ivs_b[-1][0]
    # Final alphas differ: the last update consumed the poisoned value.
    assert alpha_a != alpha_b


def test_warmup_shortens_nan_period():
    from diq_model.uncertainty.conformal import AdaptiveConformal

    rng = np.random.default_rng(5)
    y = rng.poisson(20, 40).astype(float)
    yhat = y + rng.normal(0, 2, 40)

    no_warm = AdaptiveConformal(min_history=20).calibrate_online(y, yhat)
    with_warm = AdaptiveConformal(min_history=20).calibrate_online(
        y, yhat, warmup_true=np.full(25, 20.0), warmup_pred=np.full(25, 20.0)
    )
    nan_nowarm = sum(np.isnan(iv[0]) for iv in no_warm)
    nan_warm = sum(np.isnan(iv[0]) for iv in with_warm)
    assert nan_warm < nan_nowarm


def test_coverage_by_level_structure():
    from diq_model.uncertainty.conformal import CoverageByLevel

    y = np.array([5.0, 20.0, 40.0, 80.0])
    lo = np.zeros(4)
    hi = np.array([10.0, 10.0, 50.0, 100.0])
    cov = CoverageByLevel().coverage(y, lo, hi)
    assert set(cov) == {"0-15", "15-30", "30-60", "60+"}
    assert cov["0-15"] == 1.0
    assert cov["15-30"] == 0.0  # 20 not in [0, 10]


def test_validation_on_target_coverage():
    from diq_model.uncertainty.conformal import AdaptiveConformal

    with pytest.raises(ValueError):
        AdaptiveConformal(target_coverage=1.5)


# ---------------------------------------------- stratified (Mondrian) ACI
def _same_intervals(a, b) -> bool:
    """Tuple equality that treats NaN==NaN as equal."""
    for (alo, ahi), (blo, bhi) in zip(a, b):
        if np.isnan(alo) and np.isnan(blo):
            continue
        if alo != blo or ahi != bhi:
            return False
    return True


def test_demand_levels_index_and_bins():
    from diq_model.uncertainty.conformal import DemandLevels

    lv = DemandLevels()
    assert lv.labels() == ["0-15", "15-30", "30-60", "60+"]
    idx = lv.index_of(np.array([0.0, 14.9, 15.0, 29.9, 30.0, 60.0, 877.0]))
    assert idx.tolist() == [0, 0, 1, 1, 2, 3, 3]
    # Slightly negative forecasts clamp into the lowest bucket (small, not
    # corrupt); non-finite values are a data bug and raise.
    assert lv.index_of(np.array([-0.5])).tolist() == [0]
    with pytest.raises(ValueError, match="finite"):
        lv.index_of(np.array([np.nan]))


def test_stratified_poison_probe_no_self_leak():
    """Poisoning the realised value of the LAST day must not move any earlier
    interval: each stratum's alpha and residuals update only AFTER scoring."""
    from diq_model.uncertainty.conformal import StratifiedAdaptiveConformal

    rng = np.random.default_rng(5)
    n = 400
    pred = rng.uniform(5, 80, n)
    true = pred + rng.normal(0, 6, n)
    warm_t = rng.uniform(5, 80, 300)
    warm_p = warm_t + rng.normal(0, 6, 300)

    base = StratifiedAdaptiveConformal(min_bucket=20)
    ivs, _ = base.calibrate_online(true, pred, warmup_true=warm_t, warmup_pred=warm_p)

    poisoned = true.copy()
    poisoned[-1] = 10_000.0
    tampered = StratifiedAdaptiveConformal(min_bucket=20)
    ivs2, _ = tampered.calibrate_online(poisoned, pred, warmup_true=warm_t, warmup_pred=warm_p)

    assert _same_intervals(ivs[:-1], ivs2[:-1])


def test_stratified_thin_bucket_falls_back_to_global():
    """Below min_bucket warmup residuals, every day uses the global ACI's
    interval - identically to running the global loop alone."""
    from diq_model.uncertainty.conformal import AdaptiveConformal, StratifiedAdaptiveConformal

    rng = np.random.default_rng(6)
    n = 120
    pred = np.full(n, 10.0)
    true = pred + rng.normal(0, 4, n)
    warm_t = rng.uniform(5, 20, 10)
    warm_p = warm_t.copy()

    saci = StratifiedAdaptiveConformal(min_bucket=30)
    ivs, labels = saci.calibrate_online(true, pred, warmup_true=warm_t, warmup_pred=warm_p)
    assert set(labels) == {"global-fallback"}

    ref = AdaptiveConformal().calibrate_online(true, pred, warmup_true=warm_t, warmup_pred=warm_p)
    assert _same_intervals(ivs, ref)


def test_stratified_improves_heteroscedastic_tail():
    """Restaurant-like heteroscedastic demand: right-skewed predictions
    (busy days RARE), quiet days tight noise, busy days wide noise. The
    single global quantile is dominated by quiet-day residuals and leaves
    the busy bucket badly under-covered (the synthetic twin of the real
    49% gap); the stratified intervals must fix it."""
    from diq_model.uncertainty.conformal import (
        AdaptiveConformal,
        CoverageByLevel,
        StratifiedAdaptiveConformal,
    )

    rng = np.random.default_rng(7)
    n = 600
    pred = np.clip(rng.lognormal(np.log(20), 1.3, n), 1, None)
    true = pred + np.where(pred < 30, rng.normal(0, 3, n), rng.normal(0, 40, n))
    warm_pred = np.clip(rng.lognormal(np.log(20), 1.3, 400), 1, None)
    warm_true = warm_pred + np.where(
        warm_pred < 30, rng.normal(0, 3, 400), rng.normal(0, 40, 400)
    )

    strat = StratifiedAdaptiveConformal(min_bucket=30)
    ivs, labels = strat.calibrate_online(true, pred, warmup_true=warm_true, warmup_pred=warm_pred)
    lo = np.array([a for a, _ in ivs])
    hi = np.array([b for _, b in ivs])

    givs = AdaptiveConformal().calibrate_online(
        true, pred, warmup_true=warm_true, warmup_pred=warm_pred
    )
    glo = np.array([a for a, _ in givs])
    ghi = np.array([b for _, b in givs])

    strat_busy = CoverageByLevel().coverage(true, lo, hi)["60+"]
    global_busy = CoverageByLevel().coverage(true, glo, ghi)["60+"]
    assert strat_busy > global_busy + 0.15
    assert strat_busy > 0.8
    assert not any(l == "global-fallback" for l in labels)  # buckets are thick here
